# Testing Excalibook

## Automated: `./xb smoke`

Eight checks over DevTools, in the app's own WebView (no Android input). They
leave the canvas as they found it and delete their `xb-smoke*` files from
Download.

| Check | What it proves |
|---|---|
| boot | Excalidraw is up, the channel to Android exists; reports whether File System Access is on |
| persist | a drawing (shapes, arrow, text) survives `am force-stop` and a cold start |
| export | PNG and SVG export work, and the SVG embeds the font (fonts load from the app) |
| incoming | Open with a `.excalidraw` file replaces the canvas and takes the file's name |
| saveback | (Android 17+) Ctrl+S, sent to the page over DevTools, writes the opened drawing back to its file; the check reads the file back |
| library | Open with a `.excalidrawlib` file adds its items to the library |
| image | a shared PNG lands on the canvas as an image |
| offline | no `blocked network request` and no `not in the app` lines in the log |

## Results, 0.1.0 (2026-10-02)

| Device | Android / WebView | Smoke | By hand (who) |
|---|---|---|---|
| Acer Googlebook 14 (Intel) | 17 / 155 | 8/8 (0.1.0 release build, built and installed on the Googlebook) | Jesse: the list below, passed 2026-10-02 |
| Emulator `excalibook-37` (x86_64 tablet) | 17 (API 37.2) / 149 | 8/8 | By hand: draw with keys and mouse; Ctrl+S opens the Save dialog with the name filled in, a second Ctrl+S writes back to the same file; Ctrl+O, edit, Ctrl+S writes back; Files > tap a `.excalidraw` file > Open with offers Excalibook > edit > Ctrl+S writes back to it (file read back: valid, all elements) |
| Emulator `excalibook` (x86_64 tablet) | 16 (API 36) / 133 | 7/7 (saveback skipped: no File System Access) | By hand: Escape never closes the window; Back closes the menu or the export dialog first, then the window; Ctrl+S and PNG export save to Download/ with the Open/Show bar; Open from the bar comes back through Open with (confirm dialog); Ctrl+O through the picker; Copy to clipboard; dark theme with matching system bars; About; Library (no Browse link, no Publish); Mermaid to Excalidraw (preview renders offline) |

After the 2026-10-02 review fixes, also checked on the emulators: two 9 MB
downloads fired at once arrive intact; two Open with batches back to back
both land (images stepped apart); a cold-start Open with from Files shows the
restored drawing first, then asks, then opens the file; a corrupted stored
drawing is set aside with a message; About focuses Close, keeps keys from
the canvas, and closes on Escape from inside its page; a main-frame link to
another app page is ignored; the font gate fails on an unswapped Liberation
file.

DevTools screenshots (`./xb cdp shot`) don't include Excalidraw's canvas (it
is GPU-composited); the page around it shows. To see the drawing itself, dump
the canvas: `./xb cdp eval "document.querySelector('canvas.static').toDataURL()"`.
On the emulators, `adb exec-out screencap -p` shows the real screen.

## By hand on the Acer (Jesse)

These need a person: system pickers, the keyboard and trackpad, and Files.

1. **Draw.** Open Excalibook from the launcher. Draw a rectangle (2), an
   arrow (5) and some text (8, then click). Text is hand-drawn (Excalifont).
2. **Keep.** Close the window, open it again: the drawing is still there.
3. **Save.** Ctrl+S. The Save dialog opens in Download with the name filled
   in. Save. Change something, Ctrl+S again: no dialog, and a "Saved to …"
   toast. The window title shows the file name.
4. **Open.** Menu > Reset the canvas, then Ctrl+O and pick the file you saved.
   Change it and Ctrl+S: saved to the same file, no dialog.
5. **Open from Files.** In Files, go to the Acer's own storage > Download
   (not the Downloads shortcut; see DESIGN.md "Open with") and open the
   saved `.excalidraw` file. The first time, choose Excalibook and
   **Always**; after that `.excalidraw` files open straight in Excalibook.
   Other unknown files (a `.bin`, a `.json`) don't list Excalibook at all.
   Change the drawing and press Ctrl+S: it saves back to that file.
6. **Reopen.** With that file open and unchanged, open it again from Files:
   it just reloads, no question. Change something, open it again: it asks
   "Reopen…? The changes you made since you last saved it will be lost."
   Cancel keeps your changes. Save, open it again: no question.
7. **Export.** Ctrl+Shift+E: PNG, SVG and Copy to clipboard. Paste the PNG
   into another app (Docs, Keep) to check the clipboard. Export a PNG with
   **Embed scene** on (saved as `name.excalidraw.png`), then open it from
   Files: it comes back as an editable drawing, not a flat image.
8. **Window.** Resize the window and move it between displays: the drawing
   doesn't reload. Toggle dark mode (Shift+Alt+D): the caption bar follows.
9. **Escape and Back.** With nothing selected, press Escape a few times: the
   window stays open. Open the menu (or Export, or the library) and use the
   Back gesture: it closes that first. Back again closes the window.
10. **Pen and touch**, if you use them: draw with the free-draw tool (7).
11. **Links.** Add a link to a shape (Ctrl+K), click it: it opens in Chrome.

Then clean up: delete the test files you made in Download (or keep them).

## Building for a device

```sh
./build.sh
./xb install                              # the Googlebook, from its Linux terminal (or an adb wrapper first on PATH)
ADB_SERIAL=emulator-5582 ./xb install                                    # an emulator
```
