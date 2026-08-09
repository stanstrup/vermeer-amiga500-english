#!/usr/bin/env python3
"""Create and apply IPS patches (the standard binary-diff format used across
retro game translation/romhacking) between two same-size files.

    python tools/ips.py make  original.adf translated.adf out.ips
    python tools/ips.py apply original.adf patch.ips       out.adf
"""
import sys

MAGIC = b'PATCH'
EOF = b'EOF'
MAX_CHUNK = 0xFFFF  # a record's size field is 2 bytes


def make(source, target):
    if len(source) != len(target):
        raise ValueError('IPS needs same-size source and target files')
    out = bytearray(MAGIC)
    i, n = 0, len(source)
    while i < n:
        if source[i] == target[i]:
            i += 1
            continue
        start = i
        while i < n and source[i] != target[i]:
            i += 1
        pos = start
        while pos < i:
            end = min(pos + MAX_CHUNK, i)
            chunk = target[pos:end]
            out += pos.to_bytes(3, 'big') + len(chunk).to_bytes(2, 'big') + chunk
            pos = end
    out += EOF
    return bytes(out)


def apply(source, patch):
    if patch[:5] != MAGIC:
        raise ValueError('not an IPS patch')
    out = bytearray(source)
    pos = 5
    while patch[pos:pos + 3] != EOF:
        offset = int.from_bytes(patch[pos:pos + 3], 'big')
        size = int.from_bytes(patch[pos + 3:pos + 5], 'big')
        pos += 5
        if size == 0:  # RLE record
            rle_len = int.from_bytes(patch[pos:pos + 2], 'big')
            value = patch[pos + 2]
            pos += 3
            out[offset:offset + rle_len] = bytes([value]) * rle_len
        else:
            out[offset:offset + size] = patch[pos:pos + size]
            pos += size
    return bytes(out)


def main():
    if len(sys.argv) != 5 or sys.argv[1] not in ('make', 'apply'):
        sys.exit(__doc__)
    mode, a, b, out = sys.argv[1:]
    src = open(a, 'rb').read()
    if mode == 'make':
        result = make(src, open(b, 'rb').read())
    else:
        result = apply(src, open(b, 'rb').read())
    open(out, 'wb').write(result)
    print(f'{mode}: wrote {out} ({len(result)} bytes)')


if __name__ == '__main__':
    main()
