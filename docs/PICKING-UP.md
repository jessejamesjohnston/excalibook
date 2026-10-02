# Picking up Excalibook

**Next (2026-10-02):** 0.1.0 is released; the googlebook.studio listing is
submitted (kuscher/googlebook-tech-listings#2) and waits on Alexander. Next: pick from "Deferred" and
"Ideas" below for 0.2.

## Status

- **0.1.0 released** (GitHub release `v0.1.0`, `Excalibook.apk`). Built with
  `./build.sh` in the Acer Googlebook 14's Linux terminal, installed there
  with its own adb, and checked on it: `./xb smoke` 8/8 plus the hands-on
  list in docs/TESTING.md. Also 8/8 on an Android 17 emulator and 7/7 on an
  Android 16 emulator.
- Package `com.merrimentlabs.excalibook`, published as Merriment Labs.
- Release key: `~/.config/excalibook/keystore.jks` (+ `keystore.pass`) in the
  Googlebook's Linux terminal, outside the repo, with a backup kept
  privately. Certificate SHA-256
  `23:99:41:8B:0C:48:04:AF:EA:CA:03:11:43:25:FE:C7:40:77:DF:E3:71:24:FF:D6:95:D2:AB:B3:AF:89:9A:9F`.
  Every update must be signed with it.
- Live resizing needs `./xb live-resize on` per device (an Android compat
  override; no manifest option). It is on for the Acer.

## Releasing

1. Bump `versionCode` and `versionName` in AndroidManifest.xml; add a
   CHANGELOG entry.
2. On the Googlebook's Linux terminal: `./build.sh` (signs with the release
   key), `./xb install`, `./xb smoke`, and the hands-on list.
3. `gh release create vX.Y.Z build/Excalibook.apk` with the APK's SHA-256 in
   the notes.

## Known gaps in 0.1

- Below Android 17 every save makes a new file in Download (no save-back).
  WebView there offers File System Access but can't show a save picker.
- A drawing's file link doesn't survive closing the app: after a restart the
  drawing is still on the canvas, but Ctrl+S asks where to save.
- After Ctrl+O on Android 16 and earlier, the drawing keeps an "Untitled"
  name rather than the file's (Excalidraw's own behavior on that path).
- The empty-library hint is reworded in English only.

## Deferred from the 2026-10-02 code review

- UI scaling for low vision: the WebView pins text zoom to 100% and the
  viewport can't be zoomed, so the menus don't grow with the system font size.
  Needs a deliberate UI-scale setting (CSS zoom on Excalidraw's UI breaks its
  pointer mapping).
- Bounded transfers: files are capped at 100 MB, but chunks aren't
  acknowledged and a save back is buffered whole in Java. A protocol with
  acknowledged chunks and a temp file would lift the cap.
- Named local drafts and a recent-drawings list (opening a file replaces the
  one canvas today).

## Ideas, not started

- A short demo video for the README (the screenshots are done: docs/images,
  taken on the Acer with tools/readme_images.py, window frame included).
- Excalidraw 0.18.1's own Ctrl+Shift+S never fires (its key test misses the
  shifted "S"); Excalibook binds it itself. Drop that when Excalidraw fixes it.
- Remember the last file across restarts (persisted URI permission plus a
  handle rebuilt at startup).
