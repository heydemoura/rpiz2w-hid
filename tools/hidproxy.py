#!/usr/bin/env python3
"""Minimal client for the HID proxy (Raspberry Pi or ESP32 firmware) (stdlib only).

Library use:
    from hidproxy import HidProxy
    with HidProxy("rpiz2w-hid.local", token="secret") as hid:
        hid.type_text("hello\\n")
        hid.key("cmd+space")
        hid.cmd("MOVETO 100 200")

CLI use (each argument is one protocol line; no args = read lines from stdin):
    python3 tools/hidproxy.py --host rpiz2w-hid.local "TYPE hi" "KEY enter"
"""
import argparse
import socket
import sys


class HidProxyError(RuntimeError):
    pass


class HidProxy:
    def __init__(self, host, port=5555, token=None, timeout=30.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.rfile = self.sock.makefile("r", encoding="ascii", newline="\n")
        if token:
            self.cmd("AUTH " + token)

    def cmd(self, line):
        """Send one protocol line, return the response payload or raise on ERR."""
        if "\n" in line or "\r" in line:
            raise ValueError("protocol lines cannot contain newlines")
        self.sock.sendall(line.encode("ascii") + b"\n")
        resp = self.rfile.readline()
        if not resp:
            raise HidProxyError("connection closed by device")
        resp = resp.rstrip("\r\n")
        if resp.startswith("ERR"):
            raise HidProxyError(resp[4:])
        return resp[3:]

    def type_text(self, text):
        escaped = text.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t")
        return self.cmd("TYPE " + escaped)

    def key(self, *combos):
        return self.cmd("KEY " + " ".join(combos))

    def close(self):
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="rpiz2w-hid.local")
    ap.add_argument("--port", type=int, default=5555)
    ap.add_argument("--token")
    ap.add_argument("lines", nargs="*", help="protocol lines, e.g. 'TYPE hello' 'KEY enter'")
    args = ap.parse_args()

    lines = args.lines or (l.rstrip("\n") for l in sys.stdin)
    status = 0
    with HidProxy(args.host, args.port, args.token) as hid:
        for line in lines:
            if not line.strip():
                continue
            try:
                payload = hid.cmd(line)
                print("OK" + (" " + payload if payload else ""))
            except HidProxyError as e:
                print("ERR " + str(e))
                status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
