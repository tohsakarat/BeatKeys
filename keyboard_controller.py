"""Shortcut dispatch; callers report execution results in the GUI."""

import ctypes
import re
import time
import keyboard


# Virtual key and E0 scan code for keys whose extended identity must be retained.
EXTENDED_KEYS = {
    "insert": (0x2D, 0x52),
    "delete": (0x2E, 0x53),
    "home": (0x24, 0x47),
    "end": (0x23, 0x4F),
    "page up": (0x21, 0x49),
    "page down": (0x22, 0x51),
    "up": (0x26, 0x48),
    "down": (0x28, 0x50),
    "left": (0x25, 0x4B),
    "right": (0x27, 0x4D),
    "right ctrl": (0xA3, 0x1D),
    "right alt": (0xA5, 0x38),
    "windows": (0x5B, 0x5B),
    "left windows": (0x5B, 0x5B),
    "right windows": (0x5C, 0x5C),
    "menu": (0x5D, 0x5D),
    "print screen": (0x2C, 0x37),
    "num lock": (0x90, 0x45),
    "num enter": (0x0D, 0x1C),
    "num divide": (0x6F, 0x35),
}


def _extended_key(key):
    # Normalization collapses num enter/divide into ordinary enter/slash.
    name = key.strip().lower()
    return EXTENDED_KEYS.get(name) or EXTENDED_KEYS.get(keyboard.normalize_name(name))


def _send_key(key, release=False):
    extended_key = _extended_key(key)
    if extended_key is None:
        keyboard.send(key, do_press=not release, do_release=release)
        return

    # The keyboard library drops the extended flag. Restore it on both events.
    send_event = ctypes.windll.user32.keybd_event
    send_event.argtypes = (
        ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_ulong, ctypes.c_size_t,
    )
    send_event.restype = None
    flags = 0x0001 | (0x0002 if release else 0)
    virtual_key, scan_code = extended_key
    send_event(virtual_key, scan_code, flags, 0)


def send_hotkey(hotkey):
    for step in re.split(r",\s?", hotkey):
        keys = re.split(r"\s?\+\s?", step) if len(step) > 1 else [step]
        if not any(_extended_key(key) is not None for key in keys):
            keyboard.send(step)
            continue
        for key in keys:
            _send_key(key)
        for key in reversed(keys):
            _send_key(key, release=True)


class KeyboardController:
    def __init__(self, cooldown_seconds=0.8):
        self.cooldown_seconds = cooldown_seconds
        self._last_trigger_time = 0.0

    def trigger_hotkey(self, hotkey, command_name=None):
        now = time.monotonic()
        if now - self._last_trigger_time < self.cooldown_seconds:
            return False
        send_hotkey(hotkey)
        self._last_trigger_time = now
        return True
