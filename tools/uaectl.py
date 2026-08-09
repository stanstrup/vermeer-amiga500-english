#!/usr/bin/env python3
"""Drive FS-UAE from the command line so a translated disk can be tested.

FS-UAE grabs the mouse in relative mode, so absolute positioning is done by
closed loop: screenshot, locate the Amiga pointer, nudge, repeat.

Commands:
    start [adf]           boot FS-UAE with a disk image
    shot <out.png>        capture the emulator window
    moveto <x> <y>        move the Amiga pointer to screen coords (Amiga pixels)
    click <x> <y>         moveto, then left-click
    type <text>           type text on the Amiga keyboard
    key <name>            press a single key (enter, esc, space, f1 ...)
    wait <seconds>
    stop
"""
import ctypes
import ctypes.wintypes as wt
import os
import shutil
import subprocess
import sys
import time

import numpy as np
from PIL import Image, ImageGrab

FS_UAE = r'Z:\spil\FS-UAE\Windows\x86-64\fs-uae.exe'
HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, '..', 'build', 'uae')
CONFIG = os.path.join(WORK, 'vermeer.fs-uae')

# The Amiga screen is 320x256 (PAL, lores); the FS-UAE window shows it scaled.
AMIGA_W, AMIGA_H = 320, 256

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


class RECT(ctypes.Structure):
    _fields_ = [('left', wt.LONG), ('top', wt.LONG), ('right', wt.LONG), ('bottom', wt.LONG)]


def find_window():
    """Return (hwnd, x, y, w, h) of the FS-UAE client area."""
    result = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value.startswith('FS-UAE') and user32.IsWindowVisible(hwnd):
                result.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    if not result:
        raise SystemExit('FS-UAE window not found')
    hwnd = result[0]
    r = RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return hwnd, pt.x, pt.y, r.right, r.bottom


def focus(hwnd):
    """Bring FS-UAE to the front, and make sure it gets there.

    FS-UAE only holds the mouse while it is the foreground window, and
    Windows refuses SetForegroundWindow from a process that is not already
    foreground.  Attaching to the current foreground thread's input queue
    lifts that restriction; without this, everything still "works" except
    that the emulator quietly ignores every mouse event.
    """
    if user32.GetForegroundWindow() == hwnd:
        return True
    kernel32 = ctypes.windll.kernel32
    target = user32.GetWindowThreadProcessId(hwnd, None)
    current = kernel32.GetCurrentThreadId()
    fore = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    for thread in {target, fore} - {current}:
        user32.AttachThreadInput(current, thread, True)
    user32.ShowWindow(hwnd, 9)          # SW_RESTORE
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    for thread in {target, fore} - {current}:
        user32.AttachThreadInput(current, thread, False)
    time.sleep(0.3)
    return user32.GetForegroundWindow() == hwnd


def grab():
    """Capture the emulator client area as a PIL image."""
    hwnd, x, y, w, h = find_window()
    focus(hwnd)
    return ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)


def red_mask(img):
    a = np.asarray(img.convert('RGB')).astype(np.int16)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    return (r > 140) & (r - g > 60) & (r - b > 60)


def _tip(mask, img):
    """Hotspot of the pointer blob: leftmost pixel of its topmost row."""
    ys, xs = np.nonzero(mask)
    if len(xs) < 8:
        return None
    top = ys.min()
    tip_x = xs[ys <= top + 1].min()
    return (tip_x / (img.width / AMIGA_W), top / (img.height / AMIGA_H))


def mouse_rel(dx, dy):
    user32.mouse_event(MOUSEEVENTF_MOVE, int(dx), int(dy), 0, None)


# Host mouse units per Amiga pixel, as delivered by FS-UAE's relative mouse.
# Only meaningful with pointer acceleration off (see set_acceleration).
HOST_PER_PIXEL = 2.0

SPI_GETMOUSE, SPI_SETMOUSE = 0x0003, 0x0004
ACCEL_FILE = os.path.join(WORK, 'mouse-accel.txt')


def set_acceleration(on):
    """Turn Windows pointer acceleration off (and back on again).

    Injected relative mouse motion is scaled non-linearly while "enhance
    pointer precision" is enabled, which makes dead reckoning impossible.
    The previous setting is saved so it can be restored.
    """
    vals = (ctypes.c_int * 3)()
    user32.SystemParametersInfoW(SPI_GETMOUSE, 0, ctypes.byref(vals), 0)
    if on:
        if os.path.exists(ACCEL_FILE):
            with open(ACCEL_FILE) as f:
                saved = [int(v) for v in f.read().split()]
            vals[0], vals[1], vals[2] = saved
            os.remove(ACCEL_FILE)
        else:
            vals[2] = 1
    else:
        if not os.path.exists(ACCEL_FILE):
            os.makedirs(os.path.dirname(ACCEL_FILE), exist_ok=True)
            with open(ACCEL_FILE, 'w') as f:
                f.write(f'{vals[0]} {vals[1]} {vals[2]}')
        vals[0], vals[1], vals[2] = 0, 0, 0
    user32.SystemParametersInfoW(SPI_SETMOUSE, 0, ctypes.byref(vals), 0)
    return list(vals)


def home():
    """Park the Amiga pointer in the top-left corner, a known absolute origin."""
    for _ in range(8):
        mouse_rel(-500, -500)
        time.sleep(0.05)
    time.sleep(0.25)


def regrab():
    """Click into the window to make FS-UAE take the mouse again."""
    hwnd, x, y, w, h = find_window()
    focus(hwnd)
    user32.SetCursorPos(x + w // 2, y + h - 30)
    time.sleep(0.3)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, None)
    time.sleep(0.08)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)
    time.sleep(0.5)


def ensure_grab():
    """Make sure FS-UAE is foreground and holding the mouse.

    Without the grab, injected mouse events go to the desktop and the game
    silently ignores everything.  Detected by checking whether the host
    cursor stays pinned when the mouse is moved.
    """
    hwnd, *_ = find_window()
    focus(hwnd)
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    before = (pt.x, pt.y)
    mouse_rel(8, 8)
    time.sleep(0.15)
    user32.GetCursorPos(ctypes.byref(pt))
    if (pt.x, pt.y) != before:
        regrab()
    mouse_rel(-8, -8)
    return True


def _moved_to(before, after, step):
    """Where the pointer went, from the pixels a known move changed.

    Colour is useless here -- the game draws menu items in the same red as
    the pointer, and swaps pointer shapes between screens -- but the pointer
    is the only thing that moves when the mouse moves.  The changed pixels
    form two blobs, the old position and the new one; the new one is the
    further along the direction of travel.
    """
    a = np.asarray(before.convert('RGB')).astype(np.int16)
    b = np.asarray(after.convert('RGB')).astype(np.int16)
    diff = np.abs(a - b).sum(axis=2) > 30
    pts = np.argwhere(diff)
    if len(pts) < 4:
        return None
    v = np.array([step[1], step[0]], dtype=float)
    v /= np.linalg.norm(v)
    proj = pts @ v
    new = pts[proj > (proj.min() + proj.max()) / 2]
    mask = np.zeros_like(diff)
    mask[new[:, 0], new[:, 1]] = True
    return _tip(mask, after)


def _refine(img, near, radius=14):
    """Locate the red pointer within a small box around an expected position.

    Restricting the search keeps distant red UI text out of the way.
    """
    sx, sy = img.width / AMIGA_W, img.height / AMIGA_H
    x0, y0 = int((near[0] - radius) * sx), int((near[1] - radius) * sy)
    x1, y1 = int((near[0] + radius) * sx), int((near[1] + radius) * sy)
    x0, y0 = max(0, x0), max(0, y0)
    box = img.crop((x0, y0, min(img.width, x1), min(img.height, y1)))
    tip = _tip(red_mask(box), box)
    if tip is None:
        return None
    return (tip[0] + x0 / sx, tip[1] + y0 / sy)


def _appeared(before, after, step):
    """Tip of the pointer at its new position after a known mouse move.

    Two ways, in order of preference:

    * red that appeared -- the pointer is usually red and nothing else red
      moves, so this ignores the game's red menu text outright;
    * any pixels that changed -- needed because the pointer takes its colours
      from each screen's palette and is not always red.  The changed pixels
      form an old blob and a new one; the new one lies further along the
      direction of travel.
    """
    tip = _tip(red_mask(after) & ~red_mask(before), after)
    if tip is not None:
        return tip
    a = np.asarray(before.convert('RGB')).astype(np.int16)
    b = np.asarray(after.convert('RGB')).astype(np.int16)
    pts = np.argwhere(np.abs(a - b).sum(axis=2) > 30)
    if len(pts) < 12:
        return None
    v = np.array([step[1], step[0]], dtype=float)
    v /= np.linalg.norm(v)
    proj = pts @ v
    new = pts[proj > (proj.min() + proj.max()) / 2]
    if len(new) < 6:
        return None
    # a pointer is small; a screen redraw is not
    h = new[:, 0].max() - new[:, 0].min()
    w = new[:, 1].max() - new[:, 1].min()
    if h > 60 or w > 60:
        return None
    mask = np.zeros(a.shape[:2], dtype=bool)
    mask[new[:, 0], new[:, 1]] = True
    return _tip(mask, after)


def find_pointer(step=(30, 22)):
    """Current pointer position in Amiga pixels (nudges the mouse to find it)."""
    before = grab()
    mouse_rel(*step)
    time.sleep(0.2)
    return _appeared(before, grab(), step)


def moveto(tx, ty, tries=8):
    """Walk the Amiga pointer to (tx, ty) in Amiga pixels."""
    pos = find_pointer()
    if pos is None:
        regrab()
        pos = find_pointer()
        if pos is None:
            raise SystemExit('cannot see the Amiga pointer '
                             '(hidden by the game, or the mouse is not grabbed)')
    gain = HOST_PER_PIXEL
    for _ in range(tries):
        dx, dy = tx - pos[0], ty - pos[1]
        if abs(dx) < 1.5 and abs(dy) < 1.5:
            return pos
        step = (dx * gain, dy * gain)
        before = grab()
        mouse_rel(round(step[0]), round(step[1]))
        time.sleep(0.15)
        new = _appeared(before, grab(), step)
        if new is None:
            return pos
        moved = max(abs(new[0] - pos[0]), abs(new[1] - pos[1]))
        want = max(abs(dx), abs(dy))
        if moved > 1 and want > 1:
            gain = max(0.3, min(8.0, gain * want / moved))
        pos = new
    return pos


def _highlight_y(box):
    """Amiga y of the red (hovered) menu entry inside box=(x0,y0,x1,y1), or None."""
    img = grab()
    sx, sy = img.width / AMIGA_W, img.height / AMIGA_H
    crop = img.crop((int(box[0] * sx), int(box[1] * sy),
                     int(box[2] * sx), int(box[3] * sy)))
    ys, _xs = np.nonzero(red_mask(crop))
    if len(ys) < 6:
        return None
    return ys.mean() / sy + box[1]


def pick(tx, ty):
    """Click a menu entry.

    Kept as a separate name from click() because menu rows are only seven
    pixels apart; the game also paints the *current* city red, so the hover
    highlight cannot be used to confirm the aim -- there are two reds on
    screen and no way to tell them apart.
    """
    click(tx, ty)


def click(tx, ty):
    moveto(tx, ty)
    # the game polls the mouse every few frames; pressing immediately after
    # the move can be read at the old position
    time.sleep(0.6)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, None)
    time.sleep(0.08)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)
    time.sleep(0.3)


VK = {
    'enter': 0x0D, 'return': 0x0D, 'esc': 0x1B, 'space': 0x20, 'tab': 0x09,
    'up': 0x26, 'down': 0x28, 'left': 0x25, 'right': 0x27, 'backspace': 0x08,
    'f1': 0x70, 'f2': 0x71, 'f3': 0x72, 'f4': 0x73, 'f5': 0x74,
}


def press(vk):
    # SDL reads raw scancodes, so send those rather than virtual keys alone
    scan = user32.MapVirtualKeyW(vk, 0)
    user32.keybd_event(vk, scan, 0x0008, 0)          # KEYEVENTF_SCANCODE
    time.sleep(0.05)
    user32.keybd_event(vk, scan, 0x0008 | 0x0002, 0)  # ... | KEYEVENTF_KEYUP
    time.sleep(0.10)


def type_text(text):
    hwnd, *_ = find_window()
    focus(hwnd)
    for ch in text:
        if ch == '\n':
            press(0x0D)
            continue
        vk = user32.VkKeyScanW(ord(ch))
        shift = (vk >> 8) & 1
        if shift:
            user32.keybd_event(0x10, 0, 0, 0)
        press(vk & 0xFF)
        if shift:
            user32.keybd_event(0x10, 0, 2, 0)


def wait_for_window(timeout=60):
    """Block until the FS-UAE window exists (it takes a moment to appear)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            return find_window()
        except SystemExit:
            time.sleep(1)
    raise SystemExit('FS-UAE never opened a window')


def wait_stable(timeout=60, quiet=1.2, poll=0.6):
    """Wait until the screen stops changing, e.g. after a disk load.

    Far more reliable than fixed sleeps: floppy loads vary by many seconds
    depending on what the game caches, and a click sent too early lands on
    whatever screen comes next.
    """
    deadline = time.time() + timeout
    prev = np.asarray(grab().convert('RGB')).astype(np.int16)
    still = 0.0
    while time.time() < deadline:
        time.sleep(poll)
        cur = np.asarray(grab().convert('RGB')).astype(np.int16)
        changed = (np.abs(cur - prev).sum(axis=2) > 30).sum()
        prev = cur
        # the mouse pointer alone is a few dozen pixels; ignore that much
        still = still + poll if changed < 80 else 0.0
        if still >= quiet:
            return True
    return False


def _differs(a, b, threshold=2000):
    x = np.asarray(a.convert('RGB')).astype(np.int16)
    y = np.asarray(b.convert('RGB')).astype(np.int16)
    return (np.abs(x - y).sum(axis=2) > 30).sum() > threshold


def wait_next(reference, timeout=90, quiet=1.2, poll=0.5):
    """Wait for the screen to become a different screen, then settle.

    Stability alone is not enough: the Amiga sits on a static black screen for
    seconds at a time while loading from floppy, and a click sent then is
    swallowed or lands on whatever appears next.  So first wait for a
    substantial change from the frame captured before the action, and only
    then wait for the new screen to stop moving.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _differs(reference, grab()):
            break
        time.sleep(poll)
    else:
        return False
    return wait_stable(timeout=max(5, deadline - time.time()), quiet=quiet, poll=poll)


def act(fn, timeout=90, quiet=1.2):
    """Do something, then wait for the screen it leads to."""
    before = grab()
    fn()
    return wait_next(before, timeout=timeout, quiet=quiet)


def click_next(x, y, timeout=90, quiet=1.2):
    return act(lambda: click(x, y), timeout=timeout, quiet=quiet)


def write_config(adf):
    """Write an FS-UAE config that boots a *copy* of the disk image.

    The game can write save files back to the floppy, so the image under test
    is never the one in the repository.
    """
    os.makedirs(WORK, exist_ok=True)
    kick = os.path.join(WORK, 'kick13.rom')
    if not os.path.exists(kick):
        raise SystemExit(f'missing Kickstart ROM at {kick}')
    disk = os.path.join(WORK, 'test.adf')
    if os.path.abspath(adf) != os.path.abspath(disk):
        shutil.copyfile(adf, disk)
    with open(CONFIG, 'w') as f:
        f.write(f"""[config]
amiga_model = A500
kickstart_file = {kick}
floppy_drive_0 = {os.path.abspath(disk)}
floppy_drive_speed = 800
fullscreen = 0
window_width = 640
window_height = 512
zoom = full
automatic_input_grab = 1
initial_input_grab = 1
title_bar = 0
floppy_overlays = 0
video_sync = 0
""")


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cmd = sys.argv[1]
    if cmd == 'start':
        adf = sys.argv[2] if len(sys.argv) > 2 else os.path.join(WORK, 'test.adf')
        write_config(adf)
        subprocess.Popen([FS_UAE, CONFIG], cwd=WORK)
        time.sleep(6)
        print('started:', find_window())
    elif cmd == 'shot':
        grab().save(sys.argv[2])
        print('saved', sys.argv[2])
    elif cmd == 'where':
        print(find_pointer(grab()))
    elif cmd == 'moveto':
        print(moveto(float(sys.argv[2]), float(sys.argv[3])))
    elif cmd == 'pick':
        print(pick(float(sys.argv[2]), float(sys.argv[3])))
    elif cmd == 'click':
        click(float(sys.argv[2]), float(sys.argv[3]))
        print('clicked', sys.argv[2], sys.argv[3])
    elif cmd == 'type':
        type_text(' '.join(sys.argv[2:]))
    elif cmd == 'key':
        hwnd, *_ = find_window()
        focus(hwnd)
        press(VK[sys.argv[2].lower()])
    elif cmd == 'wait':
        time.sleep(float(sys.argv[2]))
    elif cmd == 'stop':
        subprocess.run(['taskkill', '/IM', 'fs-uae.exe', '/F'], capture_output=True)
        print('stopped')
    else:
        raise SystemExit(f'unknown command {cmd}')


if __name__ == '__main__':
    main()
