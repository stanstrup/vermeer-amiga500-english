#!/usr/bin/env python3
"""Build a translated Vermeer disk from the German original plus translations.

    python tools/build.py --lang en [-o output.adf] [--report]
    python tools/build.py --lang da [-o output.adf] [--report]

Three text sources are patched:

  vermeer      the executable's inline string literals, addressed by file
               offset (translation/<lang>/exe.json).  Slot sizes never change.
  DATEN.VAM    a CRLF line table of menu entries, place names, painters and
               numeric game data, addressed by line index
               (translation/<lang>/daten.json).  Lengths are free.
  v.his/A..N   historical event text, one file per period, replaced wholesale
               from translation/<lang>/vhis/*.txt.  Lengths are free.

Every step is verified: the executable is re-scanned to prove no literal moved
and no byte outside a literal changed, the numeric lines of DATEN.VAM must come
through untouched, and the rebuilt filesystem is checked for bad checksums,
cross-linked blocks and bitmap errors.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adf as adflib
import exestr

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
SOURCE = os.path.join(ROOT, 'Vermeer (1988)(Ariolasoft)(DE).adf')

EXE = '/vermeer'
DATEN = '/DATEN.VAM'
VHIS = [f'/v.his/{c}' for c in 'ABCDEFGHIJKLMN']


def load(trans, name):
    """Load a translation file, dropping '_'-prefixed keys used for comments."""
    with open(os.path.join(trans, name), encoding='utf-8') as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith('_')}


def patch_exe(disk, trans, report):
    data = disk.read_file(EXE)
    wanted = {int(k, 16): v for k, v in load(trans, 'exe.json').items()}
    slots = {s.offset: s for s in exestr.scan(data)}

    too_long = []
    for off, text in wanted.items():
        if off not in slots:
            raise SystemExit(f'exe.json: 0x{off:06x} is not a string literal')
        if len(text.encode('latin-1')) > slots[off].size:
            too_long.append((off, slots[off], text))
    if too_long:
        for off, slot, text in too_long:
            print(f'  0x{off:06x} needs {len(text)} bytes, slot has {slot.size}: '
                  f'{slot.text!r} -> {text!r}')
        raise SystemExit(f'{len(too_long)} translation(s) do not fit their slot')

    patched = exestr.patch(data, wanted)
    problems = exestr.verify(data, patched, wanted)
    if problems:
        raise SystemExit('executable verification failed:\n  ' + '\n  '.join(problems))

    if report is not None:
        for off in sorted(wanted):
            report.append(f'exe  0x{off:06x}  {slots[off].text!r} -> {wanted[off]!r}')
    disk.write_file(EXE, patched)
    return len(wanted)


def _group_width(lines, idx):
    """Longest German entry in the run of text lines around `idx`.

    The game lays these tables out in fixed columns and pads entries to the
    width of the longest one.  An entry that is longer than the German
    original the layout was built around overflows that padding: on the
    overview screen an over-long menu entry makes the game die with
    "Error 5" (illegal function call), presumably a negative SPACE$.
    """
    def is_text(i):
        return 0 <= i < len(lines) and lines[i].strip() != '' and \
            not lines[i].strip().lstrip('-').replace('.', '').isdigit()

    lo = hi = idx
    while is_text(lo - 1):
        lo -= 1
    while is_text(hi + 1):
        hi += 1
    return max(len(lines[i]) for i in range(lo, hi + 1))


def patch_daten(disk, trans, report):
    raw = disk.read_file(DATEN).decode('latin-1')
    lines = raw.split('\r\n')
    wanted = {int(k): v for k, v in load(trans, 'daten.json').items()}
    for idx, text in wanted.items():
        if idx >= len(lines):
            raise SystemExit(f'daten.json: line {idx} is past the end of the file')
        if lines[idx].strip().lstrip('-').replace('.', '').isdigit():
            raise SystemExit(f'daten.json: line {idx} is numeric game data '
                             f'({lines[idx]!r}) and must not be translated')
        budget = _group_width(lines, idx)
        if len(text) > budget:
            raise SystemExit(
                f'daten.json: line {idx} {text!r} is {len(text)} characters, but the '
                f'longest German entry in its table is {budget} '
                f'({lines[idx]!r}) - longer entries overflow the fixed-width '
                f'layout and can crash the game')
        if report is not None:
            report.append(f'daten {idx:4}  {lines[idx]!r} -> {text!r}')
        lines[idx] = text
    disk.write_file(DATEN, '\r\n'.join(lines).encode('latin-1'))
    return len(wanted)


def patch_vhis(disk, trans, report):
    count = 0
    for path in VHIS:
        name = path.rsplit('/', 1)[1]
        src = os.path.join(trans, 'vhis', name + '.txt')
        if not os.path.exists(src):
            continue
        old = disk.read_file(path).decode('latin-1')
        with open(src, encoding='utf-8') as f:
            new = f.read()
        if not new.endswith('\n'):
            new += '\n'
        if new.count('\n') != old.count('\n'):
            raise SystemExit(f'{src}: has {new.count(chr(10))} lines, '
                             f'the original has {old.count(chr(10))} - the game '
                             f'reads a fixed number of lines')
        for i, (a, b) in enumerate(zip(old.split('\n'), new.split('\n'))):
            if a.strip().isdigit() and a.strip() != b.strip():
                raise SystemExit(f'{src} line {i + 1}: numeric field '
                                 f'{a.strip()!r} changed to {b.strip()!r}')
        if report is not None:
            report.append(f'vhis {name}: {len(new)} bytes')
        disk.write_file(path, new.encode('latin-1'))
        count += 1
    return count


LANGS = {
    'en': 'EN',
    'da': 'DA',
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lang', choices=sorted(LANGS), required=True,
                    help='translation to build (translation/<lang>/)')
    ap.add_argument('-o', '--output',
                    help='defaults to Vermeer (1988)(Ariolasoft)(<LANG>).adf')
    ap.add_argument('--report', action='store_true',
                    help='list every replacement made')
    args = ap.parse_args()
    tag = LANGS[args.lang]
    output = args.output or os.path.join(ROOT, f'Vermeer (1988)(Ariolasoft)({tag}).adf')
    trans = os.path.join(ROOT, 'translation', args.lang)

    disk = adflib.ADF(SOURCE)
    problems = disk.validate()
    if problems:
        raise SystemExit('the source image is already damaged:\n  ' + '\n  '.join(problems))

    report = [] if args.report else None
    n_exe = patch_exe(disk, trans, report)
    n_daten = patch_daten(disk, trans, report)
    n_vhis = patch_vhis(disk, trans, report)

    problems = disk.validate()
    if problems:
        raise SystemExit('rebuilt filesystem is damaged:\n  ' + '\n  '.join(problems))

    # nothing outside the three text sources may differ
    original = adflib.ADF(SOURCE)
    for kind, path, blk, _size in original.walk():
        if kind != 'file' or path in (EXE, DATEN) or path in VHIS:
            continue
        if original.read_file(path) != disk.read_file(path):
            raise SystemExit(f'unexpected change to {path}')

    disk.save(output)
    if report:
        print('\n'.join(report))
    print(f'{n_exe} literals, {n_daten} data lines, {n_vhis} history files translated')
    print(f'wrote {output}')


if __name__ == '__main__':
    main()
