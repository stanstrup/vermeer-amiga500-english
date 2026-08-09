#!/usr/bin/env python3
"""Read and write files inside an OFS (``DOS\\0``) Amiga disk image.

Only what Vermeer needs: list the directory tree, read a file, and replace a
file's contents at a different size (reallocating data blocks and updating the
bitmap, hash chains and checksums).

Block layout used here (all big-endian longwords, 512-byte blocks):

    file/dir header   [0]=type 2   [1]=own key  [2]=high_seq   [3]=data_size
                      [4]=first_data  [5]=checksum  [6..77]=hash/data table
                      [-51]=byte_size [-4]=name  [-2]=extension  [-1]=sec_type
    OFS data block    [0]=type 8   [1]=header key  [2]=seq  [3]=data_size
                      [4]=next_data  [5]=checksum  then up to 488 payload bytes
"""
import struct
import sys

BS = 512
TABLE_LEN = BS // 4 - 56      # 72 pointers in the hash / data-block table
OFS_PAYLOAD = BS - 24         # 488 usable bytes per OFS data block
ROOT_BLOCK = 880

T_HEADER, T_DATA, T_LIST = 2, 8, 16
ST_ROOT, ST_USERDIR, ST_FILE = 1, 2, -3


def _u32(b, o):
    return struct.unpack('>I', b[o:o + 4])[0]


def checksum(block):
    """AmigaDOS block checksum: all longwords must sum to zero."""
    total = sum(struct.unpack('>I', block[i:i + 4])[0]
                for i in range(0, BS, 4) if i != 20)
    return (-total) & 0xFFFFFFFF


def hash_name(name):
    h = len(name)
    for ch in name.upper():
        h = (h * 13 + ord(ch)) & 0x7FF
    return h % TABLE_LEN


class ADF:
    def __init__(self, path=None, data=None):
        self.data = bytearray(data if data is not None else open(path, 'rb').read())
        self.nblocks = len(self.data) // BS
        if bytes(self.data[0:3]) != b'DOS':
            raise ValueError('not an AmigaDOS disk image')
        if self.data[3] & 1:
            raise ValueError('FFS images are not supported')

    # -- raw block access ------------------------------------------------
    def blk(self, n):
        return self.data[n * BS:(n + 1) * BS]

    def put(self, n, block):
        assert len(block) == BS
        self.data[n * BS:(n + 1) * BS] = block

    def fix_checksum(self, n):
        b = bytearray(self.blk(n))
        b[20:24] = struct.pack('>I', 0)
        b[20:24] = struct.pack('>I', checksum(b))
        self.put(n, b)

    # -- directory tree --------------------------------------------------
    @staticmethod
    def name_of(block):
        n = block[BS - 80]
        return block[BS - 79:BS - 79 + n].decode('latin-1')

    def entries(self, dirblock):
        """[(name, block, sec_type)] for one directory, following hash chains."""
        b = self.blk(dirblock)
        out = []
        for i in range(TABLE_LEN):
            h = _u32(b, 24 + 4 * i)
            while h:
                eb = self.blk(h)
                out.append((self.name_of(eb), h,
                            struct.unpack('>i', eb[BS - 4:])[0]))
                h = _u32(eb, BS - 16)
        return out

    def walk(self, dirblock=ROOT_BLOCK, path=''):
        for name, blk, sec in sorted(self.entries(dirblock), key=lambda e: e[0].lower()):
            p = f'{path}/{name}'
            if sec == ST_USERDIR:
                yield ('dir', p, blk, 0)
                yield from self.walk(blk, p)
            else:
                yield ('file', p, blk, _u32(self.blk(blk), BS - 188))

    def find(self, path):
        """Resolve '/v.his/A' to its header block number."""
        blk = ROOT_BLOCK
        for part in path.strip('/').split('/'):
            for name, b, _sec in self.entries(blk):
                if name.lower() == part.lower():
                    blk = b
                    break
            else:
                raise KeyError(path)
        return blk

    # -- file contents ---------------------------------------------------
    def data_blocks(self, header):
        """Data block numbers of a file, in order, across extension blocks."""
        out = []
        cur = header
        while cur:
            b = self.blk(cur)
            for i in reversed(range(TABLE_LEN)):
                p = _u32(b, 24 + 4 * i)
                if p:
                    out.append(p)
            cur = _u32(b, BS - 8)
        return out

    def read_file(self, path):
        header = self.find(path) if isinstance(path, str) else path
        size = _u32(self.blk(header), BS - 188)
        out = bytearray()
        for db in self.data_blocks(header):
            b = self.blk(db)
            out += b[24:24 + _u32(b, 12)]
        return bytes(out[:size])

    # -- allocation ------------------------------------------------------
    def _bitmap_blocks(self):
        root = self.blk(ROOT_BLOCK)
        return [_u32(root, BS - 196 + 4 * i) for i in range(25)
                if _u32(root, BS - 196 + 4 * i)]

    def _bit(self, block_no, value=None):
        """Get/set the free bit for a block (1 = free). Blocks 0..1 are reserved."""
        idx = block_no - 2
        maps = self._bitmap_blocks()
        # each bitmap block holds a checksum longword then 127 longwords of bits
        per_map = (BS - 4) * 8
        m, rest = divmod(idx, per_map)
        word, bit = divmod(rest, 32)
        bm = self._bitmap_blocks()[m] if m < len(maps) else None
        if bm is None:
            raise IndexError(block_no)
        off = bm * BS + 4 + word * 4
        cur = struct.unpack('>I', self.data[off:off + 4])[0]
        if value is None:
            return (cur >> bit) & 1
        if value:
            cur |= (1 << bit)
        else:
            cur &= ~(1 << bit)
        self.data[off:off + 4] = struct.pack('>I', cur)
        return value

    def _refresh_bitmap_checksums(self):
        for bm in self._bitmap_blocks():
            b = bytearray(self.blk(bm))
            b[0:4] = b'\0\0\0\0'
            total = sum(struct.unpack('>I', b[i:i + 4])[0] for i in range(0, BS, 4))
            b[0:4] = struct.pack('>I', (-total) & 0xFFFFFFFF)
            self.put(bm, b)

    def alloc(self, count):
        out = []
        for n in range(2, self.nblocks):
            if len(out) == count:
                break
            if n == ROOT_BLOCK or n in self._bitmap_blocks():
                continue
            if self._bit(n):
                self._bit(n, 0)
                out.append(n)
        if len(out) < count:
            raise RuntimeError('disk full')
        return out

    def free(self, blocks):
        for n in blocks:
            self._bit(n, 1)
            self.put(n, bytearray(BS))

    # -- writing ---------------------------------------------------------
    def write_file(self, path, content):
        """Replace a file's contents; the size may change."""
        header = self.find(path) if isinstance(path, str) else path
        old = self.data_blocks(header)
        old_ext = []
        cur = _u32(self.blk(header), BS - 8)
        while cur:
            old_ext.append(cur)
            cur = _u32(self.blk(cur), BS - 8)

        need = max(1, -(-len(content) // OFS_PAYLOAD))
        n_ext = max(0, -(-need // TABLE_LEN) - 1)

        if need == len(old) and n_ext == len(old_ext):
            # Same size: keep the file exactly where it was, in the same
            # order.  Reallocating would hand back the same blocks in
            # ascending order and quietly reshuffle the file across the disk,
            # which is legal AmigaDOS but changes the physical layout.
            data_blocks, ext_blocks = old, old_ext
        else:
            self.free(old + old_ext)
            blocks = self.alloc(need + n_ext)
            data_blocks, ext_blocks = blocks[:need], blocks[need:]

        # data blocks
        for i, db in enumerate(data_blocks):
            chunk = content[i * OFS_PAYLOAD:(i + 1) * OFS_PAYLOAD]
            b = bytearray(BS)
            struct.pack_into('>IIII', b, 0, T_DATA, header, i + 1, len(chunk))
            struct.pack_into('>I', b, 16,
                             data_blocks[i + 1] if i + 1 < len(data_blocks) else 0)
            b[24:24 + len(chunk)] = chunk
            self.put(db, b)
            self.fix_checksum(db)

        # header + extension blocks each carry up to TABLE_LEN pointers,
        # stored in reverse order
        chain = [header] + ext_blocks
        for ci, cb in enumerate(chain):
            part = data_blocks[ci * TABLE_LEN:(ci + 1) * TABLE_LEN]
            b = bytearray(self.blk(cb)) if ci == 0 else bytearray(BS)
            if ci == 0:
                for i in range(TABLE_LEN):          # clear old table
                    struct.pack_into('>I', b, 24 + 4 * i, 0)
                struct.pack_into('>I', b, 8, len(part))
                struct.pack_into('>I', b, 12, 0)
                struct.pack_into('>I', b, 16, data_blocks[0])
                struct.pack_into('>I', b, BS - 188, len(content))
            else:
                struct.pack_into('>IIII', b, 0, T_LIST, cb, len(part), 0)
                struct.pack_into('>I', b, 16, part[0] if part else 0)
                struct.pack_into('>I', b, BS - 12, header)
                struct.pack_into('>i', b, BS - 4, T_LIST)
            for i, p in enumerate(part):
                struct.pack_into('>I', b, 24 + 4 * (TABLE_LEN - 1 - i), p)
            struct.pack_into('>I', b, BS - 8,
                             chain[ci + 1] if ci + 1 < len(chain) else 0)
            self.put(cb, b)
            self.fix_checksum(cb)

        # data blocks point back at the header (already set above)
        self._refresh_bitmap_checksums()

    def save(self, path):
        open(path, 'wb').write(self.data)

    # -- validation ------------------------------------------------------
    def validate(self):
        """Return a list of problems: bad checksums, cross-linked or lost blocks."""
        problems = []
        used = {}
        for kind, path, blk, size in self.walk():
            b = self.blk(blk)
            if checksum(b) != _u32(b, 20):
                problems.append(f'bad checksum on header of {path}')
            used[blk] = path
            if kind == 'file':
                chain = self.data_blocks(blk)
                ext = []
                cur = _u32(b, BS - 8)
                while cur:
                    ext.append(cur)
                    cur = _u32(self.blk(cur), BS - 8)
                for db in chain + ext:
                    if db in used:
                        problems.append(f'block {db} shared by {path} and {used[db]}')
                    used[db] = path
                    if checksum(self.blk(db)) != _u32(self.blk(db), 20):
                        problems.append(f'bad checksum on block {db} of {path}')
                total = sum(_u32(self.blk(db), 12) for db in chain)
                if total != size:
                    problems.append(f'{path}: block payload {total} != header size {size}')
        for n in used:
            if n not in (ROOT_BLOCK,) and self._bit(n):
                problems.append(f'block {n} ({used[n]}) is marked free in the bitmap')
        return problems


def main():
    adf = ADF(sys.argv[1])
    if len(sys.argv) > 2 and sys.argv[2] == 'check':
        problems = adf.validate()
        print('\n'.join(problems) if problems else 'filesystem OK')
        return 1 if problems else 0
    for kind, path, blk, size in adf.walk():
        print(f'{kind:4} {size:8} blk={blk:4} {path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
