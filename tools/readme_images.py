#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The README's screenshots (docs/images/*.png), in two steps, the way PDF
Toolbox makes its own (tools/readme_images.py there, MIT).

  tools/readme_images.py capture [NAME...]   set up each screen on a device and save raw captures
                                             in ~/.cache/excalibook/shots
  tools/readme_images.py [NAME...]           compose docs/images/*.png from them (Pillow, from
                                             ~/.cache/excalibook/venv)

Capture takes real-screen screenshots (screencap; DevTools screenshots leave
out Excalidraw's canvas) and crops them to Excalibook's window, caption bar
included, on a Googlebook in desktop windowing. On an emulator it crops to the
page. Before every shot it checks that no camera is open (a camera preview
from another app could be on screen), and Excalibook's window must be on top
of anything it overlaps. Each screen is set up over DevTools with the demo
diagram below, so no personal drawings show; the canvas and library are put
back as they were afterwards. Needs Excalibook open with inspection on
(./xb debug devtools on).
"""
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = pathlib.Path.home() / ".cache/excalibook/shots"
OUT = ROOT / "docs/images"
sys.path.insert(0, str(ROOT / "tools"))

DIAGRAM = """lib.convertToExcalidrawElements([
  {type: 'rectangle', x: 100, y: 120, width: 220, height: 110, backgroundColor: '#a5d8ff', fillStyle: 'hachure', label: {text: 'Googlebook'}},
  {type: 'ellipse', x: 460, y: 110, width: 200, height: 130, backgroundColor: '#b2f2bb', fillStyle: 'solid', label: {text: 'Excalibook'}},
  {type: 'arrow', x: 325, y: 175, width: 130, height: 0, label: {text: 'offline'}},
  {type: 'text', x: 120, y: 300, text: 'Hand-drawn diagrams, no internet needed', fontSize: 28},
])"""

MERMAID = """flowchart TD
  A[An idea] --> B(Sketch it in Excalibook)
  B --> C{OK?}
  C -->|Yes| D[Export PNG or SVG]
  C -->|Not yet| B"""

# name: JavaScript that sets the screen up, given `api` and `lib` (after the diagram is drawn)
SHOTS = {
    "hero": "api.updateScene({appState: {selectedElementIds: {[els[1].id]: true}}})",
    "dark": "api.updateScene({appState: {theme: 'dark'}})",
    "menu": "api.updateScene({appState: {openMenu: 'canvas'}})",
    "export": "api.updateScene({appState: {openDialog: {name: 'imageExport'}}})",
    "library": """(async () => {
        // Each shape with its label, as one library item.
        const withLabel = (c) => [c, ...els.filter(e => e.containerId === c.id)];
        const item = (id, name, xs) => ({id, status: 'unpublished', created: Date.now(), name,
          elements: xs.map(e => ({...e, groupIds: [id]}))});
        const [rect, ellipse, arrow] = ['rectangle', 'ellipse', 'arrow'].map(t => els.find(e => e.type === t));
        await api.updateLibrary({merge: false, libraryItems: [
          item('readme-device', 'Device', withLabel(rect)),
          item('readme-app', 'App', withLabel(ellipse)),
          item('readme-link', 'Link', withLabel(arrow)),
        ]});
        // The diagram in the space left of the sidebar.
        api.scrollToContent(els, {fitToViewport: true, viewportZoomFactor: 0.45, animate: false});
        const st = api.getAppState();
        api.updateScene({appState: {openSidebar: {name: 'default', tab: 'library'},
          scrollX: st.scrollX - 160 / st.zoom.value}});
      })()""",
    "mermaid": "api.updateScene({appState: {openDialog: {name: 'ttd', tab: 'mermaid'}}})",
    "welcome": "api.resetScene()",
    "about": """(async () => {
        document.querySelector('[data-testid=main-menu-trigger]').click();
        await new Promise(r => setTimeout(r, 400));
        [...document.querySelectorAll('.dropdown-menu-item')].find(e => e.innerText.includes('About')).click();
        await new Promise(r => setTimeout(r, 600));
        document.activeElement.blur(); // no keyboard focus ring in the picture
      })()""",
}

ALT = {
    "hero": "Excalibook with a hand-drawn diagram: a hatched Googlebook box with an offline arrow to a green Excalibook ellipse, the ellipse selected with its style panel open",
    "dark": "The same diagram in the dark theme",
    "menu": "The main menu: Open, Save to, Export image, Command palette, Find on canvas, Help, Reset the canvas, About Excalibook, Dark mode and the canvas colors",
    "export": "Export image, with a preview of the diagram and PNG, SVG and Copy to clipboard",
    "library": "The library sidebar with three reusable shapes: a device, an app and a labeled arrow",
    "mermaid": "Mermaid to Excalidraw turning a small flowchart into an editable hand-drawn diagram",
    "welcome": "The first-launch screen: the Excalibook title, Open and Help",
    "about": "About Excalibook, with what it is and every license",
}


def adb(*args, binary=False):
    serial = os.environ.get("ADB_SERIAL", "")
    cmd = ["adb", *(["-s", serial] if serial else []), *args]
    out = subprocess.run(cmd, capture_output=True, check=True).stdout
    return out if binary else out.decode()


def window_frame(pkg):
    """Excalibook's window on screen, caption included, if it's a desktop window on top."""
    dump = adb("shell", "dumpsys", "window", "windows")
    m = re.search(r"Window #\d+ Window\{\S+ \S+ " + re.escape(pkg) + r"/[^}]*\}:.*?frame=\[(\d+),(\d+)\]\[(\d+),(\d+)\]",
                  dump, re.S)
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    return x1, y1, x2 - x1, y2 - y1


def camera_closed():
    out = adb("shell", "dumpsys", "media.camera")
    live = out.split("Dumpsys from previous open session")[0]
    return not re.search(r"(?i)Device \d+ is open|Client package", live)


def capture(names):
    import smoke
    RAW.mkdir(parents=True, exist_ok=True)
    pkg = smoke.PKG
    emulator = os.environ.get("ADB_SERIAL", "").startswith("emulator-")
    if emulator:
        adb("logcat", "-c")
        adb("shell", "am", "broadcast", "-a", f"{pkg}.DEBUG", "-p", pkg, "--es", "cmd", "bounds")
        time.sleep(1)
        m = re.findall(r"bounds (\d+) (\d+) (\d+) (\d+)", adb("logcat", "-d", "-s", "Excalibook:I"))
        if not m:
            sys.exit("no bounds from the app: is it running?")
        x, y, w, h = map(int, m[-1])
    else:
        frame = window_frame(pkg)
        if not frame:
            sys.exit("Excalibook has no window on screen")
        x, y, w, h = frame
    page = smoke.Page()
    page.open()
    page.eval("document.fonts.ready.then(() => true)")
    # Put the canvas and library back afterwards.
    saved = page.eval("""(async () => { const {api} = excalibookApp; const s = api.getAppState();
      return JSON.stringify({elements: api.getSceneElementsIncludingDeleted(), theme: s.theme, name: s.name,
        viewBackgroundColor: s.viewBackgroundColor, scrollX: s.scrollX, scrollY: s.scrollY, zoom: s.zoom,
        library: await api.updateLibrary({libraryItems: [], merge: true})}); })()""")
    try:
        shoot(page, names, x, y, w, h, emulator)
    finally:
        page.eval("""(async () => { const d = JSON.parse(%s); const {api, lib} = excalibookApp;
          document.querySelector('.xb-about') && document.querySelector('.xb-about button').click();
          await api.updateLibrary({libraryItems: d.library, merge: false});
          api.updateScene({elements: d.elements, captureUpdate: lib.CaptureUpdateAction.NEVER,
            appState: {theme: d.theme, name: d.name, viewBackgroundColor: d.viewBackgroundColor, scrollX: d.scrollX,
              scrollY: d.scrollY, zoom: d.zoom, openMenu: null, openDialog: null, openSidebar: null, selectedElementIds: {}}});
          await excalibookApp.flush(); })()""" % json.dumps(saved))
        page.close()


def shoot(page, names, x, y, w, h, emulator):
    for name in names:
        # A fresh screen: close everything, light theme, the diagram centred.
        page.eval("""(async () => {
          const {api, lib} = excalibookApp;
          document.querySelector('.xb-about') && document.querySelector('.xb-about button').click();
          api.updateScene({appState: {openMenu: null, openDialog: null, openSidebar: null, openPopup: null,
            selectedElementIds: {}, theme: 'light', viewBackgroundColor: '#ffffff', name: 'Excalibook demo'}});
          const els = %s;
          api.updateScene({elements: els, captureUpdate: lib.CaptureUpdateAction.NEVER});
          api.scrollToContent(els, {fitToViewport: true, viewportZoomFactor: 0.62, animate: false});
          window.__readme = els;
          await document.fonts.ready;
        })()""" % DIAGRAM)
        time.sleep(1)
        page.eval("(async () => { const {api, lib} = excalibookApp; const els = api.getSceneElements(); %s; })()"
                  % SHOTS[name])
        if name == "export":
            # The preview renders a moment after the dialog opens.
            for _ in range(30):
                if page.eval("!!document.querySelector('.ImageExportModal__preview__canvas canvas, "
                             ".ImageExportModal__preview__canvas svg')"):
                    break
                time.sleep(0.5)
            time.sleep(1)
        if name == "mermaid":
            time.sleep(1.5)
            page.eval("(() => { const t = document.querySelector('.ttd-dialog textarea'); t.focus(); t.select(); return true; })()")
            page.ws.call("Input.insertText", text=MERMAID)
            time.sleep(3)  # the preview renders after a short pause
            page.eval("document.activeElement.blur()")
        time.sleep(2)
        if not emulator:
            frame = window_frame(smoke_pkg())
            if frame != (x, y, w, h):
                sys.exit(f"Excalibook's window moved or resized ({frame}); start again")
            if not camera_closed():
                sys.exit("a camera is open: no screenshot")
        png = adb("exec-out", "screencap", "-p", binary=True)
        (RAW / f"{name}.full.png").write_bytes(png)
        (RAW / f"{name}.json").write_text(json.dumps({"x": x, "y": y, "w": w, "h": h}))
        print(f"captured {name}")


def smoke_pkg():
    import smoke
    return smoke.PKG


def compose(names):
    from PIL import Image, ImageDraw, ImageFilter
    OUT.mkdir(parents=True, exist_ok=True)
    for name in names:
        box = json.loads((RAW / f"{name}.json").read_text())
        shot = Image.open(io.BytesIO((RAW / f"{name}.full.png").read_bytes())).convert("RGBA")
        shot = shot.crop((box["x"], box["y"], box["x"] + box["w"], box["y"] + box["h"]))
        # 1760 px wide: sharp at the README's 880 px on a 2x screen.
        width = 1760
        shot = shot.resize((width, round(shot.height * width / shot.width)), Image.LANCZOS)
        radius, pad, blur = 22, 40, 18
        mask = Image.new("L", shot.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, shot.width - 1, shot.height - 1), radius, fill=255)
        canvas = Image.new("RGBA", (shot.width + 2 * pad, shot.height + 2 * pad), (0, 0, 0, 0))
        shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle((pad, pad + 6, pad + shot.width, pad + shot.height + 6), radius,
                                                 fill=(0, 0, 0, 70))
        canvas = Image.alpha_composite(canvas, shadow.filter(ImageFilter.GaussianBlur(blur)))
        canvas.paste(shot, (pad, pad), mask)
        border = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(border).rounded_rectangle((pad, pad, pad + shot.width - 1, pad + shot.height - 1), radius,
                                                 outline=(0, 0, 0, 40), width=2)
        canvas = Image.alpha_composite(canvas, border)
        out = OUT / f"{name}.png"
        canvas.save(out, optimize=True)
        print(f"{out.relative_to(ROOT)}  {canvas.width}x{canvas.height}  {out.stat().st_size // 1024} KB")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["capture"]:
        capture(args[1:] or list(SHOTS))
    else:
        compose(args or list(SHOTS))
