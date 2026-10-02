#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# From PDF Toolbox (tools/cdp.py), copyright (c) 2026 the PDF Toolbox authors, MIT License.
"""A tiny Chrome DevTools Protocol client for the app's WebViews.

The WebView's DevTools socket is forwarded to a unix socket in the VM (not a
TCP port, which the Linux Terminal app would try to forward to the host).
Inspection has to be switched on in the app first: `./xb debug devtools on`.

  cdp.py targets                  list pages (index, url, title)
  cdp.py eval JS [-t N|substr]    evaluate JS in a page, print the JSON result
  cdp.py shot OUT.png [-t ...]    screenshot a page's WebView
  cdp.py net SECONDS [-t ...]     log network requests and failures
  cdp.py keys COMBO... [-t ...]   press keys in the page (ctrl+shift+p, f1, escape)
  cdp.py type TEXT [-t ...]       type text into the focused element
  cdp.py click X Y [right]        mouse click at page coordinates
"""
import base64
import json
import os
import re
import socket
import struct
import subprocess
import sys
import time

SERIAL = os.environ.get("ADB_SERIAL", "")  # ./xb sets it
PKG = os.environ.get("CDP_PKG", "com.merrimentlabs.excalibook")
LOCAL = os.environ.get("CDP_SOCK") or os.path.join(
    os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR") or "/tmp", f"excalibook-devtools-{os.getuid()}.sock")


def adb(*args):
    return subprocess.check_output(["adb", *(["-s", SERIAL] if SERIAL else []), *args]).decode()


def connect():
    pid = adb("shell", "pidof", PKG).split()
    if not pid:
        sys.exit(f"{PKG} is not running")
    name = f"webview_devtools_remote_{pid[0]}"
    if name not in adb("shell", "cat", "/proc/net/unix"):
        sys.exit(f"no {name}: turn inspection on with `./xb debug devtools on`")
    os.makedirs(os.path.dirname(LOCAL), exist_ok=True)
    unforward()  # a run killed midway leaves adb listening on a deleted socket
    if os.path.exists(LOCAL):
        os.unlink(LOCAL)
    adb("forward", f"localfilesystem:{LOCAL}", f"localabstract:{name}")


def unforward():
    subprocess.call(["adb", *(["-s", SERIAL] if SERIAL else []), "forward", "--remove", f"localfilesystem:{LOCAL}"],
                    stderr=subprocess.DEVNULL)


def http_json(path):
    s = socket.socket(socket.AF_UNIX)
    s.connect(LOCAL)
    s.sendall(f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
    data = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        data += chunk
        if b"\r\n\r\n" in data:
            head, body = data.split(b"\r\n\r\n", 1)
            m = re.search(rb"Content-Length:\s*(\d+)", head, re.I)
            if m and len(body) >= int(m.group(1)):
                break
    s.close()
    return json.loads(data.split(b"\r\n\r\n", 1)[1])


class Socket:
    def __init__(self, path):
        self.s = socket.socket(socket.AF_UNIX)
        self.s.connect(LOCAL)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET {path} HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                        f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                        "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += self.s.recv(1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise RuntimeError(head.decode(errors="replace"))
        self.buf = b""
        self.next_id = 0
        self.events = []

    def send(self, obj):
        payload = json.dumps(obj).encode()
        head = bytearray([0x81])
        n = len(payload)
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126)
            head += struct.pack(">H", n)
        else:
            head.append(0x80 | 127)
            head += struct.pack(">Q", n)
        mask = os.urandom(4)
        body = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        self.s.sendall(bytes(head) + mask + body)

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.s.recv(1 << 20)
            if not chunk:
                raise EOFError("devtools socket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self, timeout=None):
        self.s.settimeout(timeout)
        msg = b""
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            data = self._read(n)
            op = b0 & 0x0F
            if op in (0, 1, 2):
                msg += data
                if b0 & 0x80:
                    return json.loads(msg)

    def call(self, method, timeout=60, **params):
        self.next_id += 1
        my_id = self.next_id
        self.send({"id": my_id, "method": method, "params": params})
        while True:
            m = self.recv(timeout=timeout)
            if m.get("id") == my_id:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m["result"]
            self.events.append(m)


MODIFIERS = {"alt": 1, "ctrl": 2, "meta": 4, "shift": 8}
NAMED = {  # key, code, Windows virtual key code
    "enter": ("Enter", "Enter", 13), "escape": ("Escape", "Escape", 27), "tab": ("Tab", "Tab", 9),
    "backspace": ("Backspace", "Backspace", 8), "space": (" ", "Space", 32),
    "`": ("`", "Backquote", 192), "up": ("ArrowUp", "ArrowUp", 38), "down": ("ArrowDown", "ArrowDown", 40),
    "left": ("ArrowLeft", "ArrowLeft", 37), "right": ("ArrowRight", "ArrowRight", 39),
    **{f"f{n}": (f"F{n}", f"F{n}", 111 + n) for n in range(1, 13)},
}


def press(ws, combo):
    """Presses one key combination like ctrl+shift+p, f1 or escape."""
    *mods, key = combo.lower().split("+") if combo != "+" else ["+"]
    bits = sum(MODIFIERS[m] for m in mods)
    if key in NAMED:
        k, code, vk = NAMED[key]
    elif len(key) == 1 and key.isalnum():
        k = key.upper() if "shift" in mods else key
        code = f"Key{key.upper()}" if key.isalpha() else f"Digit{key}"
        vk = ord(key.upper())
    else:
        sys.exit(f"unknown key {key!r}")
    text = "\r" if k == "Enter" else (k if len(k) == 1 and not bits & 7 else "")
    down = {"type": "keyDown" if text else "rawKeyDown", "modifiers": bits, "key": k, "code": code,
            "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk}
    if text:
        down["text"] = down["unmodifiedText"] = text
    ws.call("Input.dispatchKeyEvent", **down)
    ws.call("Input.dispatchKeyEvent", type="keyUp", modifiers=bits, key=k, code=code,
            windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk)


def pick(targets, sel):
    pages = [t for t in targets if t.get("type") == "page"]
    if sel is None:
        # Prefer the visible page: WebView reports it as attached and not empty.
        visible = [t for t in pages if json.loads(t.get("description") or "{}").get("visible")]
        return (visible or pages)[0]
    if sel.isdigit():
        return pages[int(sel)]
    for t in pages:
        if sel in t["url"] or sel in t.get("title", ""):
            return t
    sys.exit(f"no page matches {sel!r}")


def main():
    args = sys.argv[1:]
    sel = None
    if "-t" in args:
        i = args.index("-t")
        sel = args[i + 1]
        del args[i:i + 2]
    if not args:
        sys.exit(__doc__)
    cmd, rest = args[0], args[1:]
    connect()
    try:
        targets = http_json("/json/list")
        if cmd == "targets":
            pages = [t for t in targets if t.get("type") == "page"]
            for i, t in enumerate(pages):
                d = json.loads(t.get("description") or "{}")
                flags = ",".join(k for k in ("attached", "visible", "empty") if d.get(k))
                print(f"{i}\t[{flags}]\t{t['url'][:110]}\t{t.get('title', '')[:60]}")
            return
        page = pick(targets, sel)
        ws = Socket(re.search(r"(/devtools/.*)$", page["webSocketDebuggerUrl"]).group(1))
        if cmd == "eval":
            r = ws.call("Runtime.evaluate", expression=rest[0], awaitPromise=True,
                        returnByValue=True, userGesture=True)
            if "exceptionDetails" in r:
                print("EXCEPTION:", json.dumps(r["exceptionDetails"].get("exception", r["exceptionDetails"]))[:2000])
                sys.exit(1)
            print(json.dumps(r["result"].get("value"), indent=1, ensure_ascii=False))
        elif cmd == "shot":
            # WebView only draws while its window is on screen; a covered
            # window never answers.
            # An idle WebView draws no new frames, and the capture waits for
            # one: keep a 1px element changing for a few seconds.
            ws.call("Runtime.evaluate", expression=(
                "(()=>{const d=document.createElement('div');d.style.cssText='position:fixed;left:0;top:0;"
                "width:1px;height:1px;pointer-events:none;z-index:2147483647';document.body.appendChild(d);"
                "const end=performance.now()+4000;const f=t=>{d.style.opacity=(t%2?0.01:0.02);"
                "if(t<end)requestAnimationFrame(f);else d.remove()};requestAnimationFrame(f)})()"))
            try:
                r = ws.call("Page.captureScreenshot", timeout=40, format="png")
            except (socket.timeout, TimeoutError):
                sys.exit("no frame: is the app's window visible?")
            with open(rest[0], "wb") as f:
                f.write(base64.b64decode(r["data"]))
            print(rest[0])
        elif cmd == "keys":
            # Chromium-level key presses (not Android's input pipeline), to
            # drive VS Code: cdp.py keys ctrl+shift+p escape "ctrl+`"
            for combo in rest:
                press(ws, combo)
                time.sleep(0.15)
        elif cmd == "click":
            # cdp.py click X Y [right]: a real mouse click in page coordinates
            x, y = float(rest[0]), float(rest[1])
            button = rest[2] if len(rest) > 2 else "left"
            for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
                ws.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button=button,
                        buttons=0 if kind == "mouseMoved" else (2 if button == "right" else 1), clickCount=1)
        elif cmd == "type":
            ws.call("Input.insertText", text=rest[0])
        elif cmd == "net":
            ws.call("Network.enable")
            end = time.time() + float(rest[0] if rest else 10)
            reqs = {}
            while time.time() < end:
                try:
                    m = ws.recv(timeout=max(0.1, end - time.time()))
                except (socket.timeout, TimeoutError):
                    break
                p = m.get("params", {})
                if m.get("method") == "Network.requestWillBeSent":
                    reqs[p["requestId"]] = p["request"]["method"] + " " + p["request"]["url"][:120]
                elif m.get("method") == "Network.responseReceived":
                    r = p["response"]
                    print(f"{r['status']} {'(cache) ' if r.get('fromDiskCache') else ''}{reqs.get(p['requestId'], r['url'][:120])}")
                elif m.get("method") == "Network.loadingFailed":
                    print(f"FAILED {p.get('errorText')} {reqs.get(p['requestId'], '?')}")
        else:
            sys.exit(__doc__)
    finally:
        unforward()


if __name__ == "__main__":
    main()
