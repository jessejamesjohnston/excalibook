#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The license gate and Excalibook's notices.

Reads the packages the web build actually contains (web/dist/.modules.json,
written by vite.config.ts), the fonts in the app and licenses/fonts.json,
and writes THIRD_PARTY_NOTICES.md (repository) and about.html (the app's
About & licenses page).

It stops the build when:
  - a package's license isn't on the permissive list below (GPL, AGPL, LGPL,
    MPL-only, or unknown all fail), or no license text can be found for it;
  - a font folder in the app isn't listed in licenses/fonts.json.

  licenses.py --modules web/dist/.modules.json --fonts DIR --version V \
              --md THIRD_PARTY_NOTICES.md --html OUT/about.html
"""
import argparse
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEXTS = ROOT / "licenses" / "texts"

PERMISSIVE = {"MIT", "ISC", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "0BSD", "CC0-1.0",
              "Zlib", "Unlicense", "OFL-1.1", "BlueOak-1.0.0"}

# Packages whose npm release has no license file: the text from their
# repository, kept in licenses/texts/ (checked in, with the source noted).
TEXT_FOR = {
    "@excalidraw/excalidraw": "excalidraw--excalidraw.txt",
    "fastdom": "fastdom.txt",
    "react-remove-scroll-bar": "react-remove-scroll-bar.txt",
}
TEXT_FOR_PREFIX = {"@radix-ui/": "radix-ui--primitives.txt"}

# Packages with no license field whose license file was read and checked by hand.
LICENSE_FOR = {"khroma": "MIT"}

PDF_TOOLBOX = {
    "name": "PDF Toolbox (parts of the Android code and page bridge)",
    "version": "0.6.1",
    "license": "MIT",
    "source": "https://github.com/kuscher/pdf-toolbox",
    "text": "MIT License\n\nCopyright (c) 2026 the PDF Toolbox authors\n\n",
}


def allowed(expr: str) -> bool:
    """An SPDX expression is allowed if some choice it offers is all permissive.

    A small recursive-descent parser that keeps parentheses: OR needs one
    allowed side, AND needs both, `X WITH exception` is X. A legacy
    "(MIT OR Apache-2.0)" with no other operators parses the same way. Anything
    it can't parse is refused.
    """
    tokens = re.findall(r"\(|\)|[^\s()]+", expr)
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def take(expected=None):
        nonlocal pos
        tok = peek()
        if tok is None or (expected and tok != expected):
            raise ValueError(f"bad SPDX expression {expr!r}")
        pos += 1
        return tok

    def atom():
        if peek() == "(":
            take("(")
            v = disjunction()
            take(")")
            return v
        tok = take()
        if tok in ("AND", "OR", "WITH", ")"):
            raise ValueError(f"bad SPDX expression {expr!r}")
        if peek() == "WITH":
            take("WITH")
            take()
        return tok in PERMISSIVE

    def conjunction():
        v = atom()
        while peek() == "AND":
            take("AND")
            v = atom() and v
        return v

    def disjunction():
        v = conjunction()
        while peek() == "OR":
            take("OR")
            v = conjunction() or v
        return v

    try:
        v = disjunction()
        if pos != len(tokens):
            return False
        return v
    except ValueError:
        return False


# The gate's own test, run every build: grouping must decide, not word order.
for _expr, _want in [("MIT", True), ("GPL-3.0-only", False), ("(MIT OR Apache-2.0)", True),
                     ("GPL-3.0-only AND (MIT OR Apache-2.0)", False), ("(MIT AND Zlib)", True),
                     ("MIT OR GPL-3.0-only", True), ("(MPL-2.0 OR Apache-2.0)", True),
                     ("Apache-2.0 WITH LLVM-exception", True), ("GPL-2.0-only WITH Classpath-exception-2.0", False),
                     ("MIT AND (GPL-3.0-only OR LGPL-2.1-only)", False), ("MIT OR", False), ("", False)]:
    assert allowed(_expr) is _want, f"license gate self-test: {_expr!r} should be {_want}"


def package_dirs(modules: list[str]) -> list[Path]:
    dirs = set()
    for m in modules:
        m = m.lstrip("\0")
        hit = re.search(r"(.*node_modules/)((?:@[^/]+/)?[^/]+)", m)
        if hit:
            dirs.add(Path(hit.group(1) + hit.group(2)))
    return sorted(dirs)


def license_of(pkg: dict) -> str | None:
    lic = pkg.get("license") or pkg.get("licenses")
    if isinstance(lic, dict):
        lic = lic.get("type")
    if isinstance(lic, list):
        lic = " OR ".join(x.get("type") if isinstance(x, dict) else x for x in lic)
    return lic or LICENSE_FOR.get(pkg["name"])


def license_text(d: Path, name: str) -> str | None:
    for f in sorted(d.iterdir()):
        if re.match(r"(licen[cs]e|copying)", f.name, re.I) and f.is_file():
            return f.read_text(errors="replace")
    t = TEXT_FOR.get(name) or next((v for k, v in TEXT_FOR_PREFIX.items() if name.startswith(k)), None)
    return (TEXTS / t).read_text() if t else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--modules", required=True)
    ap.add_argument("--fonts", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--webkit", required=True, help="the AndroidX WebKit version build.sh bundles")
    ap.add_argument("--md", required=True)
    ap.add_argument("--html", required=True)
    a = ap.parse_args()

    problems = []
    components = {}  # (name, version) -> entry
    for d in package_dirs(json.loads(Path(a.modules).read_text())):
        pkg = json.loads((d / "package.json").read_text())
        name, version = pkg["name"], pkg.get("version", "")
        lic = license_of(pkg)
        if not lic:
            problems.append(f"{name} {version}: no license in package.json")
            continue
        if not allowed(lic):
            problems.append(f"{name} {version}: {lic} isn't on the permissive list")
            continue
        text = license_text(d, name)
        if not text:
            problems.append(f"{name} {version}: {lic}, but no license text in the package or licenses/texts/")
            continue
        repo = pkg.get("repository")
        repo = repo.get("url") if isinstance(repo, dict) else repo
        components[(name, version)] = {"name": name, "version": version, "license": lic,
                                       "source": repo or f"https://www.npmjs.com/package/{name}",
                                       "text": text.strip() + "\n"}

    fonts = json.loads((ROOT / "licenses" / "fonts.json").read_text())
    font_entries = []
    for folder in sorted(p.name for p in Path(a.fonts).iterdir() if p.is_dir()):
        f = fonts.get(folder)
        if not f:
            problems.append(f"font folder {folder}/ isn't in licenses/fonts.json")
            continue
        if not allowed(f["license"]):
            problems.append(f"font {f['name']}: {f['license']} isn't on the permissive list")
            continue
        if f.get("text"):
            text = (TEXTS / f["text"]).read_text()
        elif f["license"] == "OFL-1.1":
            text = f["copyright"] + "\n\n" + (TEXTS / "OFL-1.1.txt").read_text()
        elif f["license"] == "MIT":
            mit = (TEXTS / "excalidraw--excalidraw.txt").read_text()
            text = "MIT License\n\n" + f["copyright"] + "\n\n" + mit.split("\n\n", 2)[2]
        else:
            problems.append(f"font {f['name']}: no text for {f['license']}")
            continue
        font_entries.append({**f, "text": text.strip() + "\n"})

    if problems:
        sys.exit("license gate failed:\n  " + "\n  ".join(problems))

    # The one Java library in the APK (build.sh passes its jar to D8).
    components[("androidx.webkit", a.webkit)] = {
        "name": "androidx.webkit", "version": a.webkit, "license": "Apache-2.0",
        "source": "https://developer.android.com/jetpack/androidx/releases/webkit",
        "text": "Copyright (C) The Android Open Source Project\n\n" + (TEXTS / "Apache-2.0.txt").read_text()}
    entries = sorted(components.values(), key=lambda e: e["name"].lower())
    write_md(Path(a.md), a.version, entries, font_entries)
    write_html(Path(a.html), a.version, entries, font_entries)
    kinds = {}
    for e in entries + font_entries:
        kinds[e["license"]] = kinds.get(e["license"], 0) + 1
    print(f"licenses: {len(entries)} packages, {len(font_entries)} fonts, all permissive: "
          + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())))


INTRO = (
    "Excalibook {v} is Excalidraw's editor (@excalidraw/excalidraw) in an Android app. "
    "Excalibook's own code is under the MIT License (LICENSE). Every part it contains "
    "keeps its own license, all of them permissive (MIT, ISC, BSD, Apache 2.0, CC0, "
    "OFL for fonts), listed below with their copyright holders and sources. The app "
    "shows the same list under About Excalibook."
)


def write_md(path: Path, version, entries, fonts):
    out = ["# Third-party notices", "",
           "<!-- Written by tools/licenses.py (build.sh runs it). Don't edit by hand. -->", "",
           INTRO.format(v=version), "",
           "Parts of the Android code (MainActivity, Web, Downloads) and the page's bridge to it "
           "(web/src/android.ts) are adapted from PDF Toolbox "
           "(https://github.com/kuscher/pdf-toolbox), MIT License, copyright (c) 2026 the PDF "
           "Toolbox authors.", "",
           "## Fonts", ""]
    for f in fonts:
        out.append(f"- **{f['name']}**, {f['license']}, {f['source']}" + (f" ({f['note']})" if f.get("note") else ""))
    out += ["", "## Libraries", "", "| Package | Version | License |", "|---|---|---|"]
    for e in entries:
        out.append(f"| {e['name']} | {e['version']} | {e['license']} |")
    out += ["", "The full license texts are in the app (About Excalibook), generated from the same list.", ""]
    path.write_text("\n".join(out))


def write_html(path: Path, version, entries, fonts):
    esc = html.escape
    parts = [f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>About Excalibook</title>
<style>
body {{ font: 15px/1.5 system-ui, sans-serif; margin: 0; padding: 20px 24px 40px; color: #1b1b1f; background: #fff; }}
@media (prefers-color-scheme: dark) {{ body {{ color: #e3e3e8; background: #232329; }} a {{ color: #a8a5ff; }} pre {{ background: #2e2d39 !important; }} }}
h1 {{ font-size: 22px; margin: 0 0 4px; }} h2 {{ font-size: 17px; margin: 28px 0 8px; }}
.sub {{ opacity: .7; margin: 0 0 16px; }}
details {{ margin: 4px 0; }} summary {{ cursor: pointer; }}
pre {{ white-space: pre-wrap; font: 12px/1.45 ui-monospace, monospace; background: #f4f4f7; padding: 10px 12px; border-radius: 8px; }}
</style></head><body>
<h1>Excalibook {esc(version)}</h1>
<p class="sub">Excalidraw's whiteboard, offline, as a Googlebook app.</p>
<p>Excalibook is <a href="https://github.com/excalidraw/excalidraw">Excalidraw</a>, the open-source
virtual whiteboard, built as an Android app: one download for Googlebooks with Intel and Snapdragon
chips. It has no internet permission. Your drawings stay on this device until you save or share
them yourself.</p>
<p>Live collaboration, share links, the online library browser and the AI tools need Excalidraw's
servers, so they aren't in Excalibook. Everything else is Excalidraw as you know it.</p>
<p>A Merriment Labs app: a personal project by Jesse Johnston, made in his own time. Not affiliated
with or endorsed by Excalidraw or by any employer. Built on the Android shell of
<a href="https://github.com/kuscher/pdf-toolbox">PDF Toolbox</a> by Alexander Kuscher.</p>
<h2>Licenses</h2>
<p>Excalibook's own code is under the MIT License. Every part keeps its own license; all of them
are permissive.</p>
<details><summary><b>Excalibook</b>, MIT</summary><pre>{esc((ROOT / 'LICENSE').read_text())}</pre></details>
<details><summary><b>{esc(PDF_TOOLBOX['name'])}</b>, MIT</summary><pre>{esc(PDF_TOOLBOX['text'] + (TEXTS / 'excalidraw--excalidraw.txt').read_text().split(chr(10) * 2, 2)[2])}</pre></details>
<h2>Fonts</h2>"""]
    for f in fonts:
        note = f"<p>{esc(f['note'])}</p>" if f.get("note") else ""
        parts.append(f"<details><summary><b>{esc(f['name'])}</b>, {esc(f['license'])}</summary>"
                     f"<p><a href=\"{esc(f['source'])}\">{esc(f['source'])}</a></p>{note}<pre>{esc(f['text'])}</pre></details>")
    parts.append(f"<h2>Libraries ({len(entries)})</h2>")
    for e in entries:
        parts.append(f"<details><summary><b>{esc(e['name'])}</b> {esc(e['version'])}, {esc(e['license'])}</summary>"
                     f"<p>{esc(str(e['source']))}</p><pre>{esc(e['text'])}</pre></details>")
    parts.append("<script>document.addEventListener('click',e=>{const a=e.target.closest('a[href^=\"http\"]');"
                 "if(a){e.preventDefault();window.top.location.href=a.href;}});</script>")
    parts.append("</body></html>\n")
    path.write_text("\n".join(parts))


if __name__ == "__main__":
    main()
