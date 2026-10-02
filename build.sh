#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Builds and signs Excalibook without Gradle, the way PDF Toolbox does: aapt2
# for resources, javac for the code, Google's D8 for dex, then zipalign and
# apksigner. Works on Debian/Ubuntu (sudo apt install aapt zipalign apksigner
# default-jdk-headless python3 python3-venv curl) on x86_64 or arm64, or with an
# Android SDK's build-tools (found through ANDROID_HOME).
#
#   ./build.sh fetch   once per machine: the Android bits Debian doesn't
#                      package (~/.cache/android, shared with PDF Toolbox),
#                      Node with npm, Liberation Sans 2.1.5 and a Python venv
#                      with fontTools (~/.cache/excalibook)
#   ./build.sh         builds build/Excalibook.apk
#
# The release key is not in the repository: it lives in ~/.config/excalibook
# (keystore.jks and keystore.pass; EXCALIBOOK_KEYS points elsewhere). Without
# it the APK is signed with a test key kept in ~/.cache/excalibook, which is
# fine for trying a build but can't update an Excalibook installed from a
# release (and vice versa).
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

C=${ANDROID_CACHE:-$HOME/.cache/android}
B=${EXCALIBOOK_CACHE:-$HOME/.cache/excalibook}
WEBKIT=1.18.0-alpha02
NODE_MAJOR=22
LIBERATION_URL=https://github.com/liberationfonts/liberation-fonts/files/7261482/liberation-fonts-ttf-2.1.5.tar.gz
LIBERATION_SHA256=7191c669bf38899f73a2094ed00f7b800553364f90e2637010a69c0e268f25d0

fetch() {
  mkdir -p "$C/dl" "$B"
  local repo=https://dl.google.com/android
  unzip_one() { python3 -c "import zipfile,sys; open(sys.argv[3],'wb').write(zipfile.ZipFile(sys.argv[1]).read(sys.argv[2]))" "$@"; }
  # Resources link against API 31: the first with pathSuffix (the .excalidraw
  # filters), and still readable by Debian's aapt2 2.19 (it can't load 37's).
  [[ -f $C/android-31.jar ]] || { curl -fSLo "$C/dl/p31.zip" $repo/repository/platform-31_r01.zip &&
    unzip_one "$C/dl/p31.zip" android-12/android.jar "$C/android-31.jar"; }
  [[ -f $C/android-37.jar ]] || { curl -fSLo "$C/dl/p37.zip" $repo/repository/platform-37.2_r01.zip &&
    unzip_one "$C/dl/p37.zip" android-37.2/android.jar "$C/android-37.jar"; }
  [[ -f $C/r8.jar ]] || curl -fSLo "$C/r8.jar" https://maven.google.com/com/android/tools/r8/8.5.35/r8-8.5.35.jar
  [[ -f $C/webkit-$WEBKIT.jar ]] || { curl -fSLo "$C/dl/webkit.aar" $repo/maven2/androidx/webkit/webkit/$WEBKIT/webkit-$WEBKIT.aar &&
    unzip_one "$C/dl/webkit.aar" classes.jar "$C/webkit-$WEBKIT.jar"; }
  rm -rf "$C/dl"
  if [[ ! -x $B/node/bin/npm ]]; then
    local v
    v=$(curl -fsSL https://nodejs.org/dist/index.json | python3 -c \
      "import json,sys; print(next(r['version'] for r in json.load(sys.stdin) if r['lts'] and r['version'].startswith('v$NODE_MAJOR.')))")
    curl -fsSL "https://nodejs.org/dist/$v/node-$v-linux-$(uname -m | sed 's/aarch64/arm64/;s/x86_64/x64/').tar.xz" | tar xJ -C "$B"
    ln -sfn "$B/node-$v-linux-"* "$B/node"
  fi
  if [[ ! -f $B/liberation-2.1.5.tar.gz ]]; then
    curl -fsSLo "$B/liberation.tmp" "$LIBERATION_URL"
    echo "$LIBERATION_SHA256  $B/liberation.tmp" | sha256sum -c --quiet
    mv "$B/liberation.tmp" "$B/liberation-2.1.5.tar.gz"
  fi
  # (--clear: a venv made before python3-venv was installed has no pip)
  [[ -x $B/venv/bin/pip ]] || python3 -m venv --clear "$B/venv"
  "$B/venv/bin/pip" install -q fonttools==4.66.1 brotli==1.2.0
  echo "toolchain in $C; node $("$B/node/bin/node" --version), fonts and venv in $B"
}

if [[ ${1:-} == fetch ]]; then fetch; exit; fi
for f in "$C/android-31.jar" "$C/android-37.jar" "$C/r8.jar" "$C/webkit-$WEBKIT.jar" \
    "$B/node/bin/npm" "$B/liberation-2.1.5.tar.gz" "$B/venv/bin/python"; do
  [[ -e $f ]] || { echo "missing $f: run ./build.sh fetch" >&2; exit 1; }
done
# aapt2, zipalign and apksigner: Debian's, else the newest SDK build-tools.
if ! command -v aapt2 >/dev/null; then
  SDK=${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Android/Sdk}}
  BT=$(ls -d "$SDK"/build-tools/* 2>/dev/null | sort -V | tail -1)
  [[ -n $BT ]] || { echo "no aapt2: sudo apt install aapt zipalign apksigner, or set ANDROID_HOME" >&2; exit 1; }
  export PATH=$BT:$PATH
fi
export PATH=$B/node/bin:$PATH

VERSION=$(sed -n 's/.*versionName="\([^"]*\)".*/\1/p' AndroidManifest.xml)
OUT=build
APK=$OUT/Excalibook.apk
rm -rf $OUT
mkdir -p $OUT/gen $OUT/classes $OUT/dex $OUT/assets

# The page: Excalidraw and our app around it (web/), built by Vite. The
# patches in web/vite.config.ts stop the build if they don't apply.
(cd web && { [[ -d node_modules && node_modules/.package-lock.json -nt package-lock.json ]] || npm ci --no-audit --no-fund; } &&
  XB_VERSION=$VERSION npx vite build --logLevel warn)
cp -a web/dist $OUT/assets/web
rm -f $OUT/assets/web/.modules.json
python3 - "$OUT/assets/web/index.html" <<'PY'
import sys, re
html = open(sys.argv[1]).read()
if re.search(r'(src|href)="https?://', html):
    sys.exit("index.html loads something from the internet")
PY
# Fonts, served from the app (window.EXCALIDRAW_ASSET_PATH = "/"), with the
# GPL Liberation Sans 1.05 swapped for the OFL 2.1.5 (tools/fonts.py).
"$B/venv/bin/python" tools/fonts.py web/node_modules/@excalidraw/excalidraw/dist/prod/fonts \
  $OUT/assets/web/fonts "$B/liberation-2.1.5.tar.gz"
# The license gate, THIRD_PARTY_NOTICES.md and the About page. It stops the
# build on anything that isn't permissively licensed.
python3 tools/licenses.py --modules web/dist/.modules.json --fonts $OUT/assets/web/fonts \
  --webkit "$WEBKIT" --version "$VERSION" --md THIRD_PARTY_NOTICES.md --html $OUT/assets/web/about.html

aapt2 compile --dir res -o $OUT/res.zip
# API 31: see fetch. (The code compiles against 37.)
aapt2 link -o $OUT/app.apk -I "$C/android-31.jar" --manifest AndroidManifest.xml \
  -A $OUT/assets --java $OUT/gen $OUT/res.zip
javac --release 17 -Xlint:all,-options,-serial,-classfile -Xmaxwarns 50 -encoding UTF-8 \
  -cp "$C/android-37.jar:$C/webkit-$WEBKIT.jar" -d $OUT/classes $(find src $OUT/gen -name '*.java')
java -cp "$C/r8.jar" com.android.tools.r8.D8 --release --min-api 34 --lib "$C/android-37.jar" \
  --output $OUT/dex $(find $OUT/classes -name '*.class') "$C/webkit-$WEBKIT.jar"

python3 - "$OUT" <<'PY'
import sys, zipfile
out = sys.argv[1]
with zipfile.ZipFile(f"{out}/app.apk", "a", zipfile.ZIP_DEFLATED) as apk:
    apk.write(f"{out}/dex/classes.dex", "classes.dex")
PY
# No native code, so one APK runs on every Googlebook, Intel or Snapdragon.
# And no INTERNET permission. Keep it that way.
python3 - "$OUT/app.apk" <<'PY'
import sys, zipfile
names = zipfile.ZipFile(sys.argv[1]).namelist()
native = [n for n in names if n.startswith("lib/") or n.endswith(".so")]
if native:
    sys.exit(f"native code in the APK ties it to one CPU architecture: {native[:5]}")
PY
PERMS=$(aapt2 dump permissions $OUT/app.apk) # a failed dump stops the build (set -e)
if grep -q 'android.permission.INTERNET' <<<"$PERMS"; then
  echo "the APK asks for INTERNET; Excalibook is offline by design" >&2; exit 1
fi
zipalign -f 4 $OUT/app.apk $OUT/aligned.apk
KEYS=${EXCALIBOOK_KEYS:-$HOME/.config/excalibook}
if [[ -f $KEYS/keystore.jks ]]; then
  KS=$KEYS/keystore.jks PASS=file:$KEYS/keystore.pass
else
  KS=$B/test-key.jks PASS=pass:test-key
  [[ -f $KS ]] || keytool -genkeypair -keystore "$KS" -storetype PKCS12 -storepass test-key -alias excalibook \
    -keyalg RSA -keysize 2048 -validity 36500 -dname "CN=Excalibook test build" 2>/dev/null
  echo "no release key in $KEYS: signing with the test key $KS" >&2
fi
apksigner sign --ks "$KS" --ks-pass "$PASS" --out $APK $OUT/aligned.apk
rm -f $APK.idsig
ls -la $APK
