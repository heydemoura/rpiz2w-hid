"""Text protocol -> HID actions. Hardware independent so it can be unit-tested.

Same protocol as the ESP32 firmware (see ../README.md): one command per line,
one response line per command ("OK", "OK <data>" or "ERR <reason>").
"""
import re

HELP = (
    "OK commands: PING | TYPE <text> | KEY <combo> [combo...] | MOVETO <x> <y> | "
    "MOVE <dx> <dy> | CLICK [left|right|middle] [count] | SCROLL <n> | "
    "SCREEN <w> <h> | DELAY <ms> | HELP"
)

# Modifier bits in byte 0 of a boot keyboard report.
MOD_LCTRL, MOD_LSHIFT, MOD_LALT, MOD_LGUI = 0x01, 0x02, 0x04, 0x08
MOD_RCTRL, MOD_RSHIFT, MOD_RALT, MOD_RGUI = 0x10, 0x20, 0x40, 0x80

BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x01, 0x02, 0x04


class Key:
    """A key press: modifier bits plus an optional HID usage code (page 0x07)."""

    __slots__ = ("mods", "code")

    def __init__(self, mods=0, code=0):
        self.mods, self.code = mods, code

    def __eq__(self, other):
        return (self.mods, self.code) == (other.mods, other.code)

    def __repr__(self):
        return f"Key(mods=0x{self.mods:02x}, code=0x{self.code:02x})"


def _build_ascii_table():
    """US layout: printable ASCII -> Key (with Shift where needed)."""
    t = {}
    for i, c in enumerate("abcdefghijklmnopqrstuvwxyz"):
        t[c] = Key(0, 0x04 + i)
        t[c.upper()] = Key(MOD_LSHIFT, 0x04 + i)
    for i, c in enumerate("1234567890"):
        t[c] = Key(0, 0x1E + i)
    for i, c in enumerate("!@#$%^&*()"):
        t[c] = Key(MOD_LSHIFT, 0x1E + i)
    plain = {" ": 0x2C, "-": 0x2D, "=": 0x2E, "[": 0x2F, "]": 0x30, "\\": 0x31,
             ";": 0x33, "'": 0x34, "`": 0x35, ",": 0x36, ".": 0x37, "/": 0x38,
             "\n": 0x28, "\t": 0x2B}
    shifted = {"_": 0x2D, "+": 0x2E, "{": 0x2F, "}": 0x30, "|": 0x31,
               ":": 0x33, '"': 0x34, "~": 0x35, "<": 0x36, ">": 0x37, "?": 0x38}
    t.update({c: Key(0, u) for c, u in plain.items()})
    t.update({c: Key(MOD_LSHIFT, u) for c, u in shifted.items()})
    return t


ASCII_KEYS = _build_ascii_table()

NAMED_KEYS = {
    "ctrl": Key(MOD_LCTRL), "control": Key(MOD_LCTRL),
    "shift": Key(MOD_LSHIFT), "alt": Key(MOD_LALT), "option": Key(MOD_LALT),
    "gui": Key(MOD_LGUI), "cmd": Key(MOD_LGUI), "command": Key(MOD_LGUI),
    "win": Key(MOD_LGUI), "super": Key(MOD_LGUI), "meta": Key(MOD_LGUI),
    "rctrl": Key(MOD_RCTRL), "rshift": Key(MOD_RSHIFT), "ralt": Key(MOD_RALT),
    "altgr": Key(MOD_RALT), "rgui": Key(MOD_RGUI),
    "enter": Key(0, 0x28), "return": Key(0, 0x28),
    "esc": Key(0, 0x29), "escape": Key(0, 0x29),
    "backspace": Key(0, 0x2A), "tab": Key(0, 0x2B), "space": Key(0, 0x2C),
    "capslock": Key(0, 0x39), "printscreen": Key(0, 0x46),
    "insert": Key(0, 0x49), "home": Key(0, 0x4A), "pageup": Key(0, 0x4B),
    "delete": Key(0, 0x4C), "del": Key(0, 0x4C), "end": Key(0, 0x4D),
    "pagedown": Key(0, 0x4E), "right": Key(0, 0x4F), "left": Key(0, 0x50),
    "down": Key(0, 0x51), "up": Key(0, 0x52), "menu": Key(0, 0x65),
    "plus": ASCII_KEYS["+"], "minus": ASCII_KEYS["-"],
}
NAMED_KEYS.update({f"f{n}": Key(0, 0x3A + n - 1) for n in range(1, 13)})


def parse_key_name(name):
    """Return the Key for a key name, or None if unknown."""
    if len(name) == 1:
        c = name.lower()
        return ASCII_KEYS.get(c) if "!" <= c <= "~" else None
    return NAMED_KEYS.get(name.lower())


def unescape(s):
    """Decode TYPE escapes (\\n \\t \\\\). Raises ValueError on bad input."""
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\":
            if i + 1 >= len(s):
                raise ValueError("dangling backslash")
            n = s[i + 1]
            if n not in "nt\\":
                raise ValueError("unknown escape \\" + n)
            out.append({"n": "\n", "t": "\t", "\\": "\\"}[n])
            i += 2
            continue
        if not (" " <= c <= "~"):
            raise ValueError("unsupported character (only printable ASCII)")
        out.append(c)
        i += 1
    return "".join(out)


def _int(s):
    try:
        return int(s, 10)
    except ValueError:
        return None


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


class Commands:
    """Executes protocol lines against a backend.

    Backend interface:
      ready() -> bool                 host has enumerated the gadget
      key_report(mods, codes)         send keyboard state (codes: up to 6 usages)
      mouse_report(buttons, x, y, wheel)  absolute x/y in 0..32767
      sleep(seconds)
    """

    def __init__(self, backend):
        self.hid = backend
        self.screen_w, self.screen_h = 1920, 1080
        self.x = self.y = 0
        self._hx = self._hy = 0

    # -- HID helpers -------------------------------------------------------
    def tap(self, mods, codes):
        self.hid.key_report(mods, codes)
        self.hid.key_report(0, [])

    def type_char(self, c):
        k = ASCII_KEYS[c]
        self.tap(k.mods, [k.code])

    def move_to(self, x, y):
        self.x = _clamp(x, 0, self.screen_w - 1)
        self.y = _clamp(y, 0, self.screen_h - 1)
        self._hx = self.x * 32767 // max(self.screen_w - 1, 1)
        self._hy = self.y * 32767 // max(self.screen_h - 1, 1)
        self.hid.mouse_report(0, self._hx, self._hy, 0)

    # -- protocol ----------------------------------------------------------
    def execute(self, line):
        # verb, one separator char, then the raw remainder (TYPE keeps its spacing)
        m = re.match(r"\s*(\S*)\s?(.*)", line.rstrip("\r\n"), re.S)
        verb, rest = m.group(1).lower(), m.group(2)
        args = rest.split()

        if not verb:
            return "ERR empty command"
        if verb == "ping":
            return "OK PONG"
        if verb == "help":
            return HELP

        if verb == "screen":
            w, h = (_int(a) for a in args) if len(args) == 2 else (None, None)
            if w is None or h is None or not (1 <= w <= 32767 and 1 <= h <= 32767):
                return "ERR usage: SCREEN <width> <height>"
            self.screen_w, self.screen_h = w, h
            self.x, self.y = _clamp(self.x, 0, w - 1), _clamp(self.y, 0, h - 1)
            return "OK"

        if verb == "delay":
            ms = _int(args[0]) if len(args) == 1 else None
            if ms is None or not 0 <= ms <= 10000:
                return "ERR usage: DELAY <0-10000 ms>"
            self.hid.sleep(ms / 1000)
            return "OK"

        if verb not in ("type", "key", "moveto", "move", "click", "scroll"):
            return f"ERR unknown command '{verb}' (try HELP)"
        if not self.hid.ready():
            return "ERR usb not connected to host"

        if verb == "type":
            try:
                text = unescape(rest)
            except ValueError as e:
                return f"ERR {e}"
            for c in text:
                self.type_char(c)
            return "OK"

        if verb == "key":
            if not args:
                return "ERR usage: KEY <combo> [combo...] e.g. KEY ctrl+c"
            combos = []  # validate everything before sending anything
            for combo in args:
                parts = _split_combo(combo)
                if parts is None:
                    return f"ERR malformed combo '{combo}'"
                mods, codes = 0, []
                for part in parts:
                    k = parse_key_name(part)
                    if k is None:
                        return f"ERR unknown key '{part}'"
                    mods |= k.mods
                    if k.code and k.code not in codes:
                        codes.append(k.code)
                if len(codes) > 6:
                    return f"ERR too many keys in '{combo}' (max 6)"
                combos.append((mods, codes))
            for mods, codes in combos:
                self.tap(mods, codes)
            return "OK"

        if verb in ("moveto", "move"):
            a, b = (_int(v) for v in args) if len(args) == 2 else (None, None)
            if a is None or b is None:
                return "ERR usage: MOVE <dx> <dy>" if verb == "move" else "ERR usage: MOVETO <x> <y>"
            if verb == "move":
                a, b = a + self.x, b + self.y
            self.move_to(a, b)
            return f"OK {self.x} {self.y}"

        if verb == "click":
            usage = "ERR usage: CLICK [left|right|middle] [1-10]"
            btn, count, i = BTN_LEFT, 1, 0
            buttons = {"left": BTN_LEFT, "right": BTN_RIGHT, "middle": BTN_MIDDLE}
            if i < len(args) and args[i].lower() in buttons:
                btn = buttons[args[i].lower()]
                i += 1
            if i < len(args):
                count = _int(args[i])
                if count is None or not 1 <= count <= 10:
                    return usage
                i += 1
            if i != len(args):
                return usage
            for n in range(count):
                self.hid.mouse_report(btn, self._hx, self._hy, 0)
                self.hid.mouse_report(0, self._hx, self._hy, 0)
                if n + 1 < count:
                    self.hid.sleep(0.04)
            return "OK"

        # scroll
        n = _int(args[0]) if len(args) == 1 else None
        if n is None or not -1000 <= n <= 1000:
            return "ERR usage: SCROLL <n> (positive=up, -1000..1000)"
        while n:
            step = _clamp(n, -127, 127)
            self.hid.mouse_report(0, self._hx, self._hy, step)
            n -= step
        return "OK"


def _split_combo(combo):
    """'ctrl+shift+t' -> ['ctrl','shift','t']; '+' alone or 'ctrl++' means the plus key."""
    parts, cur, i = [], "", 0
    while i < len(combo):
        c = combo[i]
        if c == "+" and cur:
            parts.append(cur)
            cur = ""
            if i == len(combo) - 1:
                return None  # trailing separator
        else:
            cur += c
        i += 1
    if cur:
        parts.append(cur)
    return parts or None
