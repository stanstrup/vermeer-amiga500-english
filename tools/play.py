#!/usr/bin/env python3
"""Send a sequence of actions to an already-running FS-UAE and capture screens.

    python tools/play.py click:47,150 wait:6 shot:bank

Actions:
    click:X,Y     click at Amiga screen coordinates (320x256)
    move:X,Y      move the pointer without clicking
    press         click wherever the pointer already is
    type:TEXT     type on the Amiga keyboard (use _ for space)
    key:NAME      press enter/esc/space/up/down/left/right
    wait:SECONDS
    shot[:NAME]   screenshot into build/uae/play/
    where         print the pointer position
    restore       turn Windows pointer acceleration back on

Keeping one process per turn avoids re-focusing the window for every step,
which is what makes a longer playthrough practical.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uaectl as u

OUT = os.path.join(u.WORK, 'play')
counter = [0]


def shot(name=''):
    counter[0] += 1
    os.makedirs(OUT, exist_ok=True)
    stamp = time.strftime('%H%M%S')
    path = os.path.join(OUT, f'{stamp}-{counter[0]:02d}{"-" + name if name else ""}.png')
    u.grab().save(path)
    print(path)


def main():
    u.set_acceleration(False)
    u.ensure_grab()
    for action in sys.argv[1:]:
        verb, _, arg = action.partition(':')
        if verb == 'click':
            x, y = (float(v) for v in arg.split(','))
            u.click(x, y)
            print(f'clicked {x:g},{y:g}')
        elif verb == 'move':
            x, y = (float(v) for v in arg.split(','))
            print('pointer at', u.moveto(x, y))
        elif verb == 'pick':
            x, y = (float(v) for v in arg.split(','))
            u.pick(x, y)
            print(f'picked {x:g},{y:g}')
        elif verb == 'press':
            u.user32.mouse_event(u.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, None)
            time.sleep(0.08)
            u.user32.mouse_event(u.MOUSEEVENTF_LEFTUP, 0, 0, 0, None)
            time.sleep(0.4)
            print('pressed')
        elif verb == 'type':
            u.type_text(arg.replace('_', ' '))
            print('typed', arg)
        elif verb == 'key':
            u.press(u.VK[arg.lower()])
            print('key', arg)
        elif verb == 'wait':
            time.sleep(float(arg))
        elif verb == 'shot':
            shot(arg)
        elif verb == 'where':
            print('pointer at', u.find_pointer())
        elif verb == 'restore':
            u.set_acceleration(True)
            print('pointer acceleration restored')
        else:
            raise SystemExit(f'unknown action {action!r}')


if __name__ == '__main__':
    main()
