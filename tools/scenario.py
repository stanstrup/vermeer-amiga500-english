#!/usr/bin/env python3
"""Boot a disk, play through the opening, then run a given click sequence.

    python tools/scenario.py <adf> [tag] [x,y[,wait] ...]

Each click gets a screenshot in build/uae/scenario-<tag>/.  Useful for A/B
runs: the same sequence against the German and the English image should
produce the same screens.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uaectl as u

step = 0
outdir = ''


def shot(name, wait=2.0):
    global step
    step += 1
    time.sleep(wait)
    path = os.path.join(outdir, f'{step:02d}-{name}.png')
    u.grab().save(path)
    print('  ', path)


def intro():
    """Title screen through to the main city screen, one player named JAN.

    Each step waits for the screen it leads to rather than sleeping: floppy
    loads range from two to twenty-odd seconds, and the Amiga sits on a static
    black screen while loading, so "the picture stopped moving" on its own is
    not a reliable signal.
    """
    from PIL import Image
    black = Image.new('RGB', (8, 8))
    u.wait_next(black.resize(u.grab().size), timeout=120, quiet=2.0)
    shot('title', 0)
    u.click_next(170, 188)                       # CLICK on the title
    shot('graphics', 0)
    u.click_next(174, 88)                        # extended graphics: NO
    shot('continue', 0)
    u.click_next(172, 129, timeout=120, quiet=2.0)   # continue saved game: NO
    shot('player-count', 0)
    u.click_next(156, 136, quiet=0.8)            # one player
    u.act(lambda: (u.type_text('JAN'), u.press(0x0D)), quiet=0.8)
    u.act(lambda: (u.type_text('24.05'), u.press(0x0D)), quiet=0.8)
    shot('birthday', 0)
    u.click_next(161, 95, timeout=120, quiet=2.0)    # confirm birthday
    shot('city', 0)


def main():
    global outdir
    adf = sys.argv[1]
    tag = sys.argv[2] if len(sys.argv) > 2 else 'run'
    clicks = sys.argv[3:]
    outdir = os.path.join(u.WORK, f'scenario-{tag}')
    os.makedirs(outdir, exist_ok=True)

    u.set_acceleration(False)
    try:
        u.subprocess.run(['taskkill', '/IM', 'fs-uae.exe', '/F'], capture_output=True)
        u.write_config(adf)
        u.subprocess.Popen([u.FS_UAE, u.CONFIG], cwd=u.WORK)
        print('booting', adf)
        u.wait_for_window()
        u.ensure_grab()
        intro()
        for c in clicks:
            parts = c.split(',')
            x, y = float(parts[0]), float(parts[1])
            wait = float(parts[2]) if len(parts) > 2 else 6.0
            u.click(x, y)
            shot(f'click-{x:g}-{y:g}', wait)
    finally:
        u.set_acceleration(True)


if __name__ == '__main__':
    main()
