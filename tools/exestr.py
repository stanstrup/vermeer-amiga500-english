#!/usr/bin/env python3
"""Locate and patch the string literals inside the Vermeer executable.

Vermeer was compiled with Absoft AC/BASIC, which emits every string literal
inline in the code stream:

    61 12                 BSR.B  *+2+18       ; return address = the string
    "14 Tage Aufenthalt"                      ; 18 bytes of inline data
    3F 3C 00 12           MOVE.W #18,-(SP)    ; the length, pushed separately

The BSR both pushes a pointer to the string and jumps over it. Because the
length lives in a separate MOVE.W, a literal's slot can hold a *shorter*
string with no padding: write the new text, leave the rest of the slot as
filler (the BSR skips it) and rewrite the length word.

What must never change is the slot size, since that would shift every address
after it.  Odd-length literals are padded to an even slot by the compiler, so
a slot is one byte longer than the string it holds.
"""
import struct
import sys

BSR_B = 0x61


def is_movew_imm(data, off):
    """True if a MOVE.W with an immediate source starts at off.

    The compiler does not always push the length on the stack: the opcode is
    0x3F3C for -(SP) but e.g. 0x3B3C when it goes somewhere else.  All of them
    are MOVE.W (high nibble 3) with source mode "immediate" (low byte 0x3C),
    and in every case the length is the extension word that follows.
    """
    return len(data) > off + 1 and data[off] & 0xF0 == 0x30 and data[off + 1] == 0x3C


class Slot:
    __slots__ = ('offset', 'size', 'length', 'text', 'len_off')

    def __init__(self, offset, size, length, text, len_off):
        self.offset = offset    # file offset of the string bytes
        self.size = size        # bytes reserved between the BSR and the code
        self.length = length    # declared length (== len(text))
        self.text = text        # the string itself, latin-1 decoded
        self.len_off = len_off  # file offset of the MOVE.W's length operand

    def __repr__(self):
        return f'Slot(0x{self.offset:06x}, size={self.size}, {self.text!r})'


# The MOVE.W carrying the length usually follows the string immediately, but
# the compiler sometimes emits one instruction in between.
MAX_GAP = 6


def _try_slot(d, i):
    if d[i] != BSR_B:
        return None
    if d[i + 1] == 0x00:                                # BSR.W
        disp = struct.unpack('>H', d[i + 2:i + 4])[0]
        start, size = i + 4, disp - 2
    elif d[i + 1] == 0xFF:                              # BSR.L (68020+), unused here
        return None
    else:                                               # BSR.B
        start, size = i + 2, d[i + 1]
    if not 1 <= size <= 4000 or start + size + 4 > len(d):
        return None
    for gap in range(0, MAX_GAP + 1, 2):
        at = start + size + gap
        if at + 4 > len(d) or not is_movew_imm(d, at):
            continue
        length = struct.unpack('>H', d[at + 2:at + 4])[0]
        if length == size or (length == size - 1 and d[start + size - 1] == 0):
            return Slot(start, size, length,
                        d[start:start + length].decode('latin-1'), at + 2)
    return None


def scan(data):
    """All inline string literals, in file order."""
    slots, i = [], 0
    while i < len(data) - 8:
        s = _try_slot(data, i)
        if s:
            slots.append(s)
            i = s.offset + s.size + 4
        else:
            i += 2
    return slots


def patch(data, replacements, encoding='latin-1'):
    """Apply {offset: new_text} to a copy of the executable.

    Raises if a replacement does not fit its slot, or if the offset is not a
    known literal.
    """
    out = bytearray(data)
    slots = {s.offset: s for s in scan(data)}
    for offset, text in replacements.items():
        slot = slots.get(offset)
        if slot is None:
            raise KeyError(f'0x{offset:06x} is not a string literal')
        raw = text.encode(encoding)
        if len(raw) > slot.size:
            raise ValueError(
                f'0x{offset:06x}: {text!r} needs {len(raw)} bytes, '
                f'slot holds {slot.size} (was {slot.text!r})')
        # new text, then filler the BSR jumps over.  The length goes in the
        # MOVE.W's operand word -- writing it at the end of the slot instead
        # would land on the opcode, which is how the previous attempt at this
        # translation corrupted the executable.
        out[slot.offset:slot.offset + slot.size] = raw + b'\0' * (slot.size - len(raw))
        struct.pack_into('>H', out, slot.len_off, len(raw))
    return bytes(out)


def verify(original, patched, expected, encoding='latin-1'):
    """Check that patching changed nothing structural.

    A patched slot no longer satisfies scan()'s "declared length equals slot
    size" rule -- that rule is what makes detection unambiguous on the
    *original* -- so the patched file is checked against the slots found in
    the original instead: same file size, the surrounding instructions
    untouched, and each length word agreeing with the text now in the slot.
    """
    problems = []
    if len(original) != len(patched):
        return [f'size changed: {len(original)} -> {len(patched)}']

    slots = scan(original)
    writable = set()
    for s in slots:
        writable.update(range(s.offset, s.offset + s.size))     # the text
        writable.update(range(s.len_off, s.len_off + 2))        # the length
    for i in range(len(original)):
        if original[i] != patched[i] and i not in writable:
            problems.append(f'byte 0x{i:06x} changed outside any literal slot '
                            f'(0x{original[i]:02x} -> 0x{patched[i]:02x})')

    for s in slots:
        if not is_movew_imm(patched, s.len_off - 2):
            problems.append(f'0x{s.offset:06x}: MOVE.W opcode damaged')
            continue
        length = struct.unpack('>H', patched[s.len_off:s.len_off + 2])[0]
        if length > s.size:
            problems.append(f'0x{s.offset:06x}: declared length {length} '
                            f'exceeds slot size {s.size}')
        text = patched[s.offset:s.offset + length].decode(encoding)
        want = expected.get(s.offset, s.text)
        if text != want:
            problems.append(f'0x{s.offset:06x}: expected {want!r}, found {text!r}')
    return problems


def main():
    data = open(sys.argv[1], 'rb').read()
    slots = scan(data)
    print(f'{len(slots)} inline string literals')
    for s in slots:
        print(f'0x{s.offset:06x} size={s.size:4} len={s.length:4} {s.text!r}')


if __name__ == '__main__':
    main()
