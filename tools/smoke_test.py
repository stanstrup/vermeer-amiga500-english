#!/usr/bin/env python3
"""Boot a Vermeer disk in FS-UAE and walk through it, saving a screenshot of
every screen so the translation can be eyeballed.

    python tools/smoke_test.py "Vermeer (1988)(Ariolasoft)(EN).adf"

Screens land in build/uae/shots/NN-name.png.  Coordinates are in Amiga
pixels (320x256).  The walkthrough is deliberately click-only: pressing Enter
puts the game into a keyboard mode where it hides the mouse pointer, which
the pointer tracking needs.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uaectl as u

SHOTS = os.path.join(u.WORK, 'shots')
step_no = 0


def shot(name, wait=2.0):
    global step_no
    step_no += 1
    time.sleep(wait)
    os.makedirs(SHOTS, exist_ok=True)
    path = os.path.join(SHOTS, f'{step_no:02d}-{name}.png')
    u.grab().save(path)
    print('  ', path)
    return path


def click(x, y, wait=2.0):
    u.click(x, y)
    time.sleep(wait)


def main():
    adf = sys.argv[1] if len(sys.argv) > 1 else 'Vermeer (1988)(Ariolasoft)(EN).adf'
    u.set_acceleration(False)
    try:
        u.subprocess.run(['taskkill', '/IM', 'fs-uae.exe', '/F'], capture_output=True)
        u.write_config(adf)
        u.subprocess.Popen([u.FS_UAE, u.CONFIG], cwd=u.WORK)
        print('booting', adf)
        time.sleep(30)
        shot('title', 0)

        click(170, 188, 4)          # CLICK on the title screen
        shot('graphics-prompt', 0)
        click(174, 88, 4)           # extended graphics: NO
        shot('continue-prompt', 0)
        click(172, 129, 25)         # continue saved game: NO -> loads
        shot('player-count', 0)

        click(156, 136, 3)          # one player
        shot('name-entry', 0)
        u.type_text('JAN')
        u.press(0x0D)
        time.sleep(1)
        u.type_text('24.05')
        u.press(0x0D)
        shot('birthday', 2)
        click(161, 95, 12)          # confirm birthday -> main screen
        shot('city', 0)

        for name, y in (('bank', 149.5), ('auction', 163.5),
                        ('pictures', 170.5), ('overview', 184)):
            click(47, y, 6)
            shot(name, 0)
            click(47, 176.5, 5)     # Exit back to the city screen
            shot(f'{name}-exit', 0)
    finally:
        u.set_acceleration(True)


if __name__ == '__main__':
    main()
