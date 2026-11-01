"""HID backends: the real USB gadget (/dev/hidgN) and a dry-run logger."""
import glob
import logging
import os
import select
import struct
import time

log = logging.getLogger("hidproxy.hid")


class HidWriteError(RuntimeError):
    pass


class GadgetBackend:
    """Writes reports to the configfs HID gadget created by gadget/hid-gadget.sh.

    hidg0: boot keyboard, 8-byte report  [mods, 0, k1..k6]
    hidg1: absolute mouse, 6-byte report [buttons, x lo, x hi, y lo, y hi, wheel]
    """

    def __init__(self, keyboard_dev="/dev/hidg0", mouse_dev="/dev/hidg1", timeout=1.0):
        self.keyboard_dev, self.mouse_dev, self.timeout = keyboard_dev, mouse_dev, timeout
        self._fds = {}

    def ready(self):
        # The UDC reports "configured" once the host has enumerated us.
        for path in glob.glob("/sys/class/udc/*/state"):
            try:
                with open(path) as f:
                    if f.read().strip() == "configured":
                        return True
            except OSError:
                pass
        return False

    def _write(self, dev, data):
        fd = self._fds.get(dev)
        if fd is None:
            fd = self._fds[dev] = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
        # Writes block until the host polls the endpoint; don't hang forever if
        # the host went away or is asleep.
        _, w, _ = select.select([], [fd], [], self.timeout)
        if not w:
            raise HidWriteError(f"timeout writing to {dev} (host not polling?)")
        try:
            os.write(fd, data)
        except OSError as e:
            os.close(fd)
            self._fds.pop(dev, None)
            raise HidWriteError(f"{dev}: {e.strerror}") from e

    def key_report(self, mods, codes):
        self._write(self.keyboard_dev, bytes([mods, 0] + list(codes) + [0] * (6 - len(codes))))

    def mouse_report(self, buttons, x, y, wheel):
        self._write(self.mouse_dev, struct.pack("<BHHb", buttons, x, y, wheel))

    def sleep(self, seconds):
        time.sleep(seconds)

    def release_all(self):
        try:
            if self.ready():
                self.key_report(0, [])
        except (HidWriteError, OSError):
            pass


class DryRunBackend:
    """Logs reports instead of sending them; for testing without a Pi."""

    def __init__(self):
        self.reports = []

    def ready(self):
        return True

    def key_report(self, mods, codes):
        self.reports.append(("key", mods, tuple(codes)))
        log.info("key   mods=0x%02x codes=%s", mods, " ".join(f"{c:02x}" for c in codes))

    def mouse_report(self, buttons, x, y, wheel):
        self.reports.append(("mouse", buttons, x, y, wheel))
        log.info("mouse buttons=%d x=%d y=%d wheel=%d", buttons, x, y, wheel)

    def sleep(self, seconds):
        time.sleep(seconds)

    def release_all(self):
        self.key_report(0, [])
