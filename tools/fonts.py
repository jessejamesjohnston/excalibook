#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Copies Excalidraw's fonts into the app and swaps in Liberation Sans 2.1.5.

Excalidraw 0.18 ships Liberation Sans 1.05, which is GPL-2.0 (with a font
exception). Liberation 2.x is the same design with the same metrics under
the SIL Open Font License, so drawings that use it look the same and the
app stays free of copyleft parts. Needs fontTools and brotli (build.sh
fetch puts them in a venv).

  fonts.py SRC_FONTS_DIR DEST_FONTS_DIR LIBERATION_TARBALL
"""
import io
import json
import shutil
import sys
import tarfile
from pathlib import Path

from fontTools.ttLib import TTFont

src, dest, tarball = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
TARGET = "Liberation/LiberationSans-Regular.woff2"
MEMBER = "liberation-fonts-ttf-2.1.5/LiberationSans-Regular.ttf"

if dest.exists():
    shutil.rmtree(dest)
shutil.copytree(src, dest)

old = dest / TARGET
if not old.is_file():
    sys.exit(f"fonts.py: {TARGET} is not in Excalidraw's fonts any more; check the Liberation swap")
old_names = TTFont(old)["name"]
if "1.05" not in str(old_names.getName(5, 3, 1, 0x409)):
    sys.exit(f"fonts.py: Excalidraw's Liberation Sans is no longer 1.05 ({old_names.getName(5, 3, 1, 0x409)}); recheck its license")

with tarfile.open(tarball) as tar:
    ttf = tar.extractfile(MEMBER).read()
font = TTFont(io.BytesIO(ttf))
names = font["name"]
license_text = str(names.getName(13, 3, 1, 0x409) or "")
version = str(names.getName(5, 3, 1, 0x409) or "")
if "Open Font License" not in license_text or not version.startswith("Version 2.1"):
    sys.exit(f"fonts.py: unexpected Liberation Sans ({version!r}, license {license_text[:60]!r})")
font.flavor = "woff2"
font.save(old)


def ranges(codepoints):
    """unicode-range from a font's cmap: U+20-7E,U+A0,..."""
    out, cps = [], sorted(codepoints)
    start = prev = cps[0]
    for c in cps[1:] + [None]:
        if c is not None and c == prev + 1:
            prev = c
            continue
        out.append(f"U+{start:X}" if start == prev else f"U+{start:X}-{prev:X}")
        if c is not None:
            start = prev = c
    return ",".join(out)


# Every file, not just every folder: each font's family name must be the one
# its folder is licensed for, nothing sits outside a folder, and every
# Liberation file must be the OFL 2.x release (an unswapped 1.x variant fails).
inventory = json.loads((Path(__file__).resolve().parent.parent / "licenses" / "fonts.json").read_text())
problems = []
for f in sorted(dest.rglob("*")):
    if f.is_dir():
        continue
    rel = f.relative_to(dest)
    if len(rel.parts) != 2:
        problems.append(f"{rel}: font files must be in a listed folder")
        continue
    entry = inventory.get(rel.parts[0])
    if not entry:
        problems.append(f"{rel}: folder isn't in licenses/fonts.json")
        continue
    names = TTFont(f)["name"]
    family = str(names.getName(1, 3, 1, 0x409) or names.getName(1, 1, 0, 0) or "")
    if not family.startswith(entry["family"]):
        problems.append(f"{rel}: family {family!r}, expected {entry['family']!r}")
    if rel.parts[0] == "Liberation" and not str(names.getName(5, 3, 1, 0x409) or "").startswith("Version 2."):
        problems.append(f"{rel}: not the OFL Liberation 2.x")
if problems:
    sys.exit("fonts.py: font inventory check failed:\n  " + "\n  ".join(problems))

# Excalidraw registers the canvas fonts itself, when a drawing uses them. Its
# UI (the welcome screen's hints) asks for Excalifont too, which excalidraw.com
# declares in its own page; this is ours, one face per subset file.
faces = []
for f in sorted((dest / "Excalifont").glob("*.woff2")):
    cmap = TTFont(f).getBestCmap()
    faces.append("@font-face{font-family:Excalifont;font-display:swap;"
                 f"src:url(./Excalifont/{f.name}) format(\"woff2\");unicode-range:{ranges(cmap)}}}")
if not faces:
    sys.exit("fonts.py: no Excalifont files")
(dest / "ui-fonts.css").write_text("\n".join(faces) + "\n")
print(f"fonts: {sum(1 for _ in dest.rglob('*.woff2'))} files; {TARGET} is Liberation Sans {version.split()[1]} (OFL)")
