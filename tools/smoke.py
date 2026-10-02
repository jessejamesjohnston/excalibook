#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Excalibook's end-to-end checks on a device, over DevTools (./xb smoke).

Drives the app's own WebView only (no Android input): JavaScript in the page
and the app's adb debug commands. Pickers and the keyboard are for a person:
docs/TESTING.md lists those.

  smoke.py [CHECK...]   checks: boot persist export incoming saveback library image offline

Test files are written to Download/ as xb-smoke-*; they're deleted at the end.
Needs inspection on: ./xb debug devtools on. Leaves the canvas as it found it.
"""
import base64
import json
import re
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import cdp  # noqa: E402

PKG = cdp.PKG
TMP = Path("/tmp/excalibook-smoke")
results = []


def adb(*args, check=True):
    serial = ["-s", cdp.SERIAL] if cdp.SERIAL else []
    return subprocess.run(["adb", *serial, *args], capture_output=True, text=True, check=check).stdout


class Page:
    def __init__(self):
        self.ws = None

    def open(self):
        for _ in range(30):
            try:
                cdp.connect()
                targets = cdp.http_json("/json/list")
                page = cdp.pick(targets, None)
                self.ws = cdp.Socket(re.search(r"(/devtools/.*)$", page["webSocketDebuggerUrl"]).group(1))
                if self.eval("!!(window.excalibookApp && window.excalibookApp.api)"):
                    return
            except (SystemExit, OSError, KeyError, IndexError):
                pass
            time.sleep(1)
        raise RuntimeError("the app's page never came up")

    def eval(self, js, timeout=60):
        r = self.ws.call("Runtime.evaluate", expression=js, awaitPromise=True, returnByValue=True,
                         userGesture=True, timeout=timeout)
        if "exceptionDetails" in r:
            raise RuntimeError(json.dumps(r["exceptionDetails"].get("exception", r["exceptionDetails"]))[:600])
        return r["result"].get("value")

    def close(self):
        cdp.unforward()


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}", flush=True)


def wait_for(page, js, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        v = page.eval(js)
        if v:
            return v
        time.sleep(0.5)
    return page.eval(js)


def restart(page):
    page.close()
    adb("shell", "am", "force-stop", "--user", "current", PKG)
    adb("shell", "am", "start", "--user", "current", "-n", f"{PKG}/.MainActivity")
    time.sleep(3)
    page.open()


def png(width, height, rgb):
    raw = b"".join(b"\0" + bytes(rgb) * width for _ in range(height))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def incoming(name, data):
    TMP.mkdir(exist_ok=True)
    f = TMP / name
    f.write_bytes(data)
    adb("shell", "am", "broadcast", "-a", f"{PKG}.DEBUG", "-p", PKG, "--es", "cmd", "incoming",
        "--es", "name", f"'{name}'", "--es", "b64", base64.b64encode(data).decode())


SCENE = {
    "type": "excalidraw", "version": 2, "source": "excalibook-smoke",
    "elements": [{
        "id": "smoke-rect", "type": "rectangle", "x": 100, "y": 100, "width": 240, "height": 120,
        "angle": 0, "strokeColor": "#1e1e1e", "backgroundColor": "#a5d8ff", "fillStyle": "solid",
        "strokeWidth": 2, "strokeStyle": "solid", "roughness": 1, "opacity": 100, "groupIds": [],
        "frameId": None, "index": "a0", "roundness": None, "seed": 1, "version": 1, "versionNonce": 1,
        "isDeleted": False, "boundElements": None, "updated": 1, "link": None, "locked": False,
    }],
    "appState": {"viewBackgroundColor": "#ffffff"}, "files": {},
}
LIBRARY = {"type": "excalidrawlib", "version": 2, "source": "excalibook-smoke",
           "libraryItems": [{"id": "smoke-item", "status": "unpublished", "created": 1, "name": "xb-smoke box",
                             "elements": [dict(SCENE["elements"][0], id="smoke-lib-rect")]}]}

DRAW = """(() => {
  const {api, lib} = window.excalibookApp;
  const els = lib.convertToExcalidrawElements([
    {type: 'rectangle', x: 0, y: 0, width: 200, height: 100, backgroundColor: '#ffc9c9', fillStyle: 'solid'},
    {type: 'ellipse', x: 260, y: 0, width: 120, height: 100},
    {type: 'arrow', x: 200, y: 50, width: 60, height: 0},
    {type: 'text', x: 20, y: 140, text: 'xb-smoke Excalibook'},
  ]);
  api.updateScene({elements: els, captureUpdate: lib.CaptureUpdateAction.IMMEDIATELY});
  return api.getSceneElements().length;
})()"""


def cleanup():
    """Deletes this script's test files (xb-smoke*, written by the app) from Download."""
    user = adb("shell", "am", "get-current-user").strip()
    adb("shell", "content", "delete", "--user", user, "--uri", "content://media/external/downloads",
        "--where", "\"_display_name LIKE 'xb-smoke%' AND owner_package_name='" + PKG + "'\"", check=False)


def main(only):
    page = Page()
    page.open()
    saved = page.eval("JSON.stringify({e: window.excalibookApp.api.getSceneElementsIncludingDeleted(), "
                      "s: {name: window.excalibookApp.api.getAppState().name, theme: window.excalibookApp.api.getAppState().theme, "
                      "viewBackgroundColor: window.excalibookApp.api.getAppState().viewBackgroundColor}})")
    adb("logcat", "-c", check=False)
    cleanup()
    try:
        run = lambda c: not only or c in only  # noqa: E731
        if run("boot"):
            v = page.eval("({v: excalibookApp.version, ok: !!document.querySelector('.excalidraw'), "
                          "fsa: 'showSaveFilePicker' in window, host: !!window.excalibook})")
            check("boot: Excalidraw is up", v["ok"] and v["host"], f"version {v['v']}, File System Access {v['fsa']}")
        if run("persist"):
            n = page.eval(DRAW)
            # No explicit flush: the page's own save, 300 ms after a change,
            # is what has to keep the drawing.
            time.sleep(1.5)
            restart(page)
            m = wait_for(page, "excalibookApp.api.getSceneElements().length === 4 && "
                         "excalibookApp.api.getSceneElements().find(e => e.type === 'text').text")
            check("persist: drawing survives a force-stop", n == 4 and m == "xb-smoke Excalibook", f"{n} drawn, text {m!r}")
        if run("export"):
            r = page.eval("""(async () => {
              const {api, lib} = window.excalibookApp;
              const opts = {elements: api.getSceneElements(), appState: api.getAppState(), files: api.getFiles()};
              const png = await lib.exportToBlob({...opts, mimeType: 'image/png'});
              const svg = await lib.exportToSvg({...opts, exportPadding: 10});
              const s = svg.outerHTML;
              return {png: png.size, svg: s.length, fontEmbedded: /@font-face/.test(s) && /base64/.test(s)};
            })()""")
            check("export: PNG and SVG (with the font embedded)", r["png"] > 1000 and r["fontEmbedded"],
                  f"png {r['png']} B, svg {r['svg']} chars, font embedded {r['fontEmbedded']}")
        if run("incoming"):
            page.eval("excalibookApp.api.resetScene()")
            incoming("xb-smoke-scene.excalidraw", json.dumps(SCENE).encode())
            got = wait_for(page, "(() => { const e = excalibookApp.api.getSceneElements(); "
                           "return e.length === 1 && e[0].id === 'smoke-rect' && excalibookApp.api.getAppState().name })()")
            check("incoming: Open with a .excalidraw file", got == "xb-smoke-scene", f"name {got!r}")
        if run("saveback") and page.eval("'showSaveFilePicker' in window"):
            # Ctrl+S on a drawing opened with Excalibook writes back to its
            # file (Android 17+, where File System Access is on). The check
            # opens its own test file and refuses to press Ctrl+S on anything
            # else, so it can never save into a real drawing. Keys go to the
            # page over DevTools, not through Android's input.
            page.eval("excalibookApp.api.resetScene()")
            incoming("xb-smoke-saveback.excalidraw", json.dumps(SCENE).encode())
            opened = wait_for(page, "(() => { const h = excalibookApp.api.getAppState().fileHandle; "
                              "return h && h.name === 'xb-smoke-saveback.excalidraw' && h.name })()")
            ok = False
            types = []
            if opened:
                page.eval("(() => { const {api, lib} = excalibookApp; api.updateScene({elements: [...api.getSceneElements(), "
                          "...lib.convertToExcalidrawElements([{type: 'ellipse', x: 400, y: 100, width: 80, height: 60}])], "
                          "captureUpdate: lib.CaptureUpdateAction.IMMEDIATELY}); })()")
                adb("logcat", "-c", check=False)
                cdp.press(page.ws, "ctrl+s")
                for _ in range(20):
                    if "save back ok" in adb("logcat", "-d", "-s", "Excalibook:I", check=False):
                        ok = True
                        break
                    time.sleep(0.5)
                user = adb("shell", "am", "get-current-user").strip()
                ids = re.findall(r"_id=(\d+)", adb("shell", "content", "query", "--user", user, "--uri",
                                 "content://media/external/downloads", "--projection", "_id", "--where",
                                 f"\"_display_name='{opened}' AND owner_package_name='{PKG}'\"", check=False))
                written = json.loads(adb("shell", "content", "read", "--user", user, "--uri",
                                         f"content://media/external/downloads/{ids[-1]}")) if ids else {}
                types = [e["type"] for e in written.get("elements", [])]
            check("saveback: Ctrl+S saves an opened file back to it", ok and types == ["rectangle", "ellipse"],
                  f"test file {opened!r} now holds {types}")
        if run("library"):
            incoming("xb-smoke.excalidrawlib", json.dumps(LIBRARY).encode())
            time.sleep(3)
            got = wait_for(page, "(async () => { const items = await excalibookApp.api.updateLibrary({libraryItems: [], merge: true}); "
                           "return items.some(i => i.name === 'xb-smoke box') })()")
            check("library: Open with a .excalidrawlib file", bool(got))
            page.eval("(async () => { const {api} = excalibookApp; const items = await api.updateLibrary({libraryItems: [], merge: true}); "
                      "await api.updateLibrary({libraryItems: items.filter(i => i.name !== 'xb-smoke box')}); "
                      "api.updateScene({appState: {openSidebar: null}}); })()")
        if run("image"):
            before = page.eval("excalibookApp.api.getSceneElements().length")
            incoming("xb-smoke-image.png", png(64, 48, (60, 120, 220)))
            got = wait_for(page, f"excalibookApp.api.getSceneElements().length === {before} + 1 && "
                           "excalibookApp.api.getSceneElements().at(-1).type")
            check("image: a shared PNG goes onto the canvas", got == "image", f"last element {got!r}")
        if run("offline"):
            logs = adb("logcat", "-d", "-s", "Excalibook:*", check=False)
            blocked = [l for l in logs.splitlines() if "blocked network request" in l]
            missing = [l for l in logs.splitlines() if "not in the app" in l]
            check("offline: no network requests, no missing files", not blocked and not missing,
                  "; ".join((blocked + missing)[:3]))
    finally:
        # Put the canvas back the way it was, and delete the test files.
        page.eval(f"""(() => {{ const d = JSON.parse({json.dumps(saved)}); const {{api, lib}} = window.excalibookApp;
          api.updateScene({{elements: d.e, appState: d.s, captureUpdate: lib.CaptureUpdateAction.NEVER}});
          return excalibookApp.flush(); }})()""")
        cleanup()
        page.close()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main(sys.argv[1:])
