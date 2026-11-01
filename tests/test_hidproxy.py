"""Run from rpiz2w-hid/:  python3 -m unittest discover -s tests"""
import socket
import threading
import time
import unittest

from hidproxy.commands import MOD_LCTRL, MOD_LGUI, MOD_LSHIFT, Commands, unescape
from hidproxy.server import Server


class FakeBackend:
    def __init__(self, ready=True):
        self.is_ready = ready
        self.reports = []
        self.slept = 0.0

    def ready(self):
        return self.is_ready

    def key_report(self, mods, codes):
        self.reports.append(("key", mods, tuple(codes)))

    def mouse_report(self, buttons, x, y, wheel):
        self.reports.append(("mouse", buttons, x, y, wheel))

    def sleep(self, s):
        self.slept += s

    def release_all(self):
        self.reports.append(("key", 0, ()))


RELEASE = ("key", 0, ())


class CommandsTest(unittest.TestCase):
    def setUp(self):
        self.hid = FakeBackend()
        self.c = Commands(self.hid)

    def test_ping_help_unknown_empty(self):
        self.assertEqual(self.c.execute("PING"), "OK PONG")
        self.assertEqual(self.c.execute("ping\r\n"), "OK PONG")
        self.assertTrue(self.c.execute("help").startswith("OK commands:"))
        self.assertEqual(self.c.execute("FOO"), "ERR unknown command 'foo' (try HELP)")
        self.assertEqual(self.c.execute("   "), "ERR empty command")

    def test_type(self):
        self.assertEqual(self.c.execute("TYPE aB 1!\\n"), "OK")
        self.assertEqual(self.hid.reports, [
            ("key", 0, (0x04,)), RELEASE,
            ("key", MOD_LSHIFT, (0x05,)), RELEASE,
            ("key", 0, (0x2C,)), RELEASE,
            ("key", 0, (0x1E,)), RELEASE,
            ("key", MOD_LSHIFT, (0x1E,)), RELEASE,
            ("key", 0, (0x28,)), RELEASE,
        ])

    def test_type_keeps_spacing(self):
        self.c.execute("TYPE  x")  # one separator, then a literal space
        self.assertEqual(self.hid.reports[0], ("key", 0, (0x2C,)))

    def test_type_errors(self):
        self.assertEqual(self.c.execute("TYPE a\\"), "ERR dangling backslash")
        self.assertEqual(self.c.execute("TYPE \\q"), "ERR unknown escape \\q")
        self.assertEqual(self.c.execute("TYPE é"), "ERR unsupported character (only printable ASCII)")
        self.assertEqual(self.hid.reports, [])

    def test_unescape(self):
        self.assertEqual(unescape("a\\tb\\\\c\\n"), "a\tb\\c\n")

    def test_key_combos(self):
        self.assertEqual(self.c.execute("KEY ctrl+c cmd+space enter"), "OK")
        self.assertEqual(self.hid.reports, [
            ("key", MOD_LCTRL, (0x06,)), RELEASE,
            ("key", MOD_LGUI, (0x2C,)), RELEASE,
            ("key", 0, (0x28,)), RELEASE,
        ])

    def test_key_special(self):
        self.c.execute("KEY ctrl++ + F12 shift+tab")
        self.assertEqual(self.hid.reports, [
            ("key", MOD_LCTRL | MOD_LSHIFT, (0x2E,)), RELEASE,
            ("key", MOD_LSHIFT, (0x2E,)), RELEASE,
            ("key", 0, (0x45,)), RELEASE,
            ("key", MOD_LSHIFT, (0x2B,)), RELEASE,
        ])

    def test_key_errors_send_nothing(self):
        self.assertEqual(self.c.execute("KEY ctrl+c bogus"), "ERR unknown key 'bogus'")
        self.assertEqual(self.c.execute("KEY ctrl+"), "ERR malformed combo 'ctrl+'")
        self.assertTrue(self.c.execute("KEY").startswith("ERR usage"))
        self.assertEqual(self.hid.reports, [])

    def test_moveto_and_move(self):
        self.assertEqual(self.c.execute("MOVETO 1919 1079"), "OK 1919 1079")
        self.assertEqual(self.hid.reports[-1], ("mouse", 0, 32767, 32767, 0))
        self.assertEqual(self.c.execute("MOVETO -5 99999"), "OK 0 1079")
        self.assertEqual(self.c.execute("MOVE 100 -79"), "OK 100 1000")
        self.assertEqual(self.c.execute("MOVETO 1 x"), "ERR usage: MOVETO <x> <y>")

    def test_screen(self):
        self.assertEqual(self.c.execute("SCREEN 100 100"), "OK")
        self.assertEqual(self.c.execute("MOVETO 99 0"), "OK 99 0")
        self.assertEqual(self.hid.reports[-1], ("mouse", 0, 32767, 0, 0))
        self.assertTrue(self.c.execute("SCREEN 0 10").startswith("ERR"))

    def test_click_keeps_position(self):
        self.c.execute("MOVETO 1919 0")
        self.hid.reports.clear()
        self.assertEqual(self.c.execute("CLICK right 2"), "OK")
        self.assertEqual(self.hid.reports, [
            ("mouse", 2, 32767, 0, 0), ("mouse", 0, 32767, 0, 0),
            ("mouse", 2, 32767, 0, 0), ("mouse", 0, 32767, 0, 0),
        ])
        self.assertTrue(self.c.execute("CLICK left 11").startswith("ERR"))
        self.assertTrue(self.c.execute("CLICK up").startswith("ERR"))

    def test_scroll_splits_large_values(self):
        self.assertEqual(self.c.execute("SCROLL -300"), "OK")
        self.assertEqual([r[4] for r in self.hid.reports], [-127, -127, -46])

    def test_delay(self):
        self.assertEqual(self.c.execute("DELAY 250"), "OK")
        self.assertAlmostEqual(self.hid.slept, 0.25)
        self.assertTrue(self.c.execute("DELAY 10001").startswith("ERR"))

    def test_usb_not_ready(self):
        self.hid.is_ready = False
        self.assertEqual(self.c.execute("TYPE hi"), "ERR usb not connected to host")
        self.assertEqual(self.c.execute("PING"), "OK PONG")


class ServerTest(unittest.TestCase):
    def start(self, token=None):
        self.hid = FakeBackend()
        srv = Server(self.hid, token=token)
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        threading.Thread(target=srv.serve_forever, args=("127.0.0.1", port), daemon=True).start()
        for _ in range(50):
            try:
                return socket.create_connection(("127.0.0.1", port), timeout=5), port
            except ConnectionRefusedError:
                time.sleep(0.05)
        raise RuntimeError("server did not start")

    def roundtrip(self, f, sock, line):
        sock.sendall(line.encode() + b"\n")
        return f.readline().rstrip("\n")

    def test_commands_over_tcp(self):
        sock, _ = self.start()
        f = sock.makefile("r")
        self.assertEqual(self.roundtrip(f, sock, "PING"), "OK PONG")
        self.assertEqual(self.roundtrip(f, sock, "TYPE a"), "OK")
        self.assertEqual(self.roundtrip(f, sock, "AUTH whatever"), "OK")
        self.assertEqual(self.roundtrip(f, sock, "x" * 3000), "ERR line too long")
        self.assertEqual(self.roundtrip(f, sock, "PING"), "OK PONG")
        self.assertIn(("key", 0, (0x04,)), self.hid.reports)
        sock.close()

    def test_auth(self):
        sock, _ = self.start(token="s3cret")
        f = sock.makefile("r")
        self.assertEqual(self.roundtrip(f, sock, "PING"), "ERR auth required: send AUTH <token>")
        self.assertEqual(self.roundtrip(f, sock, "AUTH nope"), "ERR bad token")
        self.assertEqual(self.roundtrip(f, sock, "AUTH s3cret"), "OK")
        self.assertEqual(self.roundtrip(f, sock, "PING"), "OK PONG")
        sock.close()

    def test_newest_client_wins(self):
        first, port = self.start()
        f1 = first.makefile("r")
        self.assertEqual(self.roundtrip(f1, first, "PING"), "OK PONG")
        second = socket.create_connection(("127.0.0.1", port), timeout=5)
        f2 = second.makefile("r")
        self.assertEqual(self.roundtrip(f2, second, "PING"), "OK PONG")
        self.assertEqual(f1.readline(), "")  # first connection was closed
        first.close()
        second.close()


if __name__ == "__main__":
    unittest.main()
