# Excalibook design

## What it is

Excalidraw's editor, the `@excalidraw/excalidraw` React component (MIT), in an
Android app for Googlebooks. It works offline with no INTERNET permission and
no native code, so one APK runs on Intel and Snapdragon. The Android shell
follows PDF Toolbox (kuscher/pdf-toolbox): Java with no Gradle, a WebView
serving the page from the APK's assets on `https://appassets.androidplatform.net`,
and a WebMessageListener channel between the page and the activity.

## Why a component, not excalidraw.com's app

The excalidraw.com app (`excalidraw-app/` upstream) is the component plus
Firebase collaboration, share links, the AI backend, Sentry and a service
worker. Each of those needs a server. Building on the component means that
code is never in the APK, so nothing has to be stripped out afterward. The
app adds the parts the component leaves to its host:

| Need | Where |
|---|---|
| Keep the drawing across restarts | `web/src/storage.ts`: scene JSON and the images it uses in IndexedDB, one transaction per save, saves queued in order; written 300 ms after each change and when the page is hidden. A failed save keeps its snapshot and retries every 5 s (one toast per failure streak). Unused images are deleted in the same transaction. Nothing is saved until the stored drawing is back on the canvas; if it can't be read, it's moved to `scene-unreadable-<time>` first and the user is told |
| The library (reusable shapes) | `storage.ts` `libraryAdapter`, through `useHandleLibrary` |
| Open with / Share | `MainActivity.takeIncoming` → chunks over the channel → `web/src/android.ts` → `App.openFiles` |
| Menu and welcome screen | `App.tsx`: Excalidraw's own items, minus Socials and collaboration, plus About |
| Window caption color | `android.ts reportColor` → `MainActivity.applyWindowColors` |

## Network: none, enforced three ways

1. The manifest has no INTERNET permission. build.sh fails if it ever does.
2. `MainActivity.Client.shouldInterceptRequest` serves the app's origin from
   assets and answers every other http(s) URL with a 403 plus a log line
   (`blocked network request:`). `./xb smoke` fails on that line.
3. Network UI is removed at build time by patches in `web/vite.config.ts`
   (the font CDN fallback, the library's Browse link and Publish item, and the
   empty-library hint that pointed at them) and in `App.tsx` (`aiEnabled`
   false, `validateEmbeddable` false, the web-embed tool hidden by CSS). Each
   patch must match exactly as many times as declared, or the build stops.

Links in drawings and in Help go to the browser (`shouldOverrideUrlLoading`).
A main-frame navigation to any other page of the app's own origin is ignored,
so the window always shows the editor (About loads in a frame).

## Files: two paths, chosen by Android version

Excalidraw saves and opens through browser-fs-access. That library uses
File System Access (`showSaveFilePicker`, `showOpenFilePicker`) when the page
has it, and a file input plus `<a download>` when it doesn't.

- **Android 17 (API 37) and later.** WebView's file chooser gained a save mode
  (`FileChooserParams.MODE_SAVE`), so `showSaveFilePicker` opens DocumentsUI's
  Save dialog (CREATE_DOCUMENT); WebView leaves the suggested name out of the
  intent, so `onShowFileChooser` adds it as EXTRA_TITLE. Ctrl+S on a drawing
  opened or saved this way writes back to the same file with no dialog. This
  is the Acer's path.
- **Android 14 to 16.** WebView still offers the API there, but every save
  picker fails at once with AbortError (seen on the API 36 emulator,
  WebView 133). `Web.HIDE_FILE_SYSTEM_ACCESS` deletes the three picker
  functions at document start, so Excalidraw uses a file input to open
  (DocumentsUI) and downloads to save. `android.ts` catches each download's
  blob and streams it to `Downloads`, which writes `Download/<name>` through
  MediaStore and shows a bar with Open and Show. Each save makes a new file.

Open with and Share deliver files as batches (begin, header and chunks per
file, end), each batch one job on the io thread, and the page handles batches
one at a time, only after the stored drawing is restored (so a cold-start
Open with can't be overwritten by it). Files over 100 MB are refused.
Everything the page sends as bytes (downloads, saves back to a file) goes
through one queue in `android.ts`, since the activity writes chunks to
whichever transfer is open; a save back's end marker carries its token.

Open with: `.excalidraw` files have no Android MIME type, so Files calls
them `application/octet-stream`. Excalibook matches them by name instead:
a VIEW filter on any type with `pathSuffix` `.excalidraw`, `.excalidrawlib`,
`.excalidraw.png` and `.excalidraw.svg` (API 31+). It never claims
octet-stream or JSON in general, so it isn't offered for other unknown files,
and "Always" makes it the default for those names only (Android saves the
matched suffix with the preference; checked on the API 37 emulator).
The limit: a name can only be matched when the link carries it. Files'
device-storage folders do (`.../document/primary:Download/x.excalidraw`);
its Downloads and Recent views hand over `.../document/msf:39`, a MediaStore
id with no name, and a generic type, so Excalibook isn't offered there. A
file that turns out not to be a drawing, library or image gets a toast.

Replacing the canvas: an opened drawing replaces it without asking when the
canvas has nothing that isn't in a file. `web/src/clean.ts` keeps a
fingerprint of the drawing as last opened from or saved to a file (content
only: it leaves out version counters and the text sizes Excalidraw
recomputes when fonts load), stored with the drawing so it survives a
restart. Saves are noticed by wrapping the save stream's close, so only a
finished write counts. Otherwise the page asks first ("Reopen…" for the
same file, "Open…" for another).

Saving back: on Android 17 and later, a file Files hands over with write
access (DocumentsUI grants read and write) gets a file handle
(`android.ts fileHandleFor`): Ctrl+S saves back to it. MainActivity keeps
the file's URI under a random token. The page's handle collects the save's
bytes in a WritableStream and sends them with that token, and MainActivity
writes them in one go with mode "wt" (truncate), once every byte has
arrived. The file's current bytes are read first; if the write fails they
are written back. If a save back fails, the token is dropped and the handle
reports itself gone, so the next Ctrl+S asks where to save. Below Android
17, or without write access, Save asks where to save. The save dialog
starts in Download (`EXTRA_INITIAL_URI`).

## Fonts and licenses

`window.EXCALIDRAW_ASSET_PATH = "/"` makes Excalidraw load its fonts from the
app. `tools/fonts.py` copies them into the assets, swaps Liberation Sans 1.05
(GPL-2.0 with a font exception) for Liberation Sans 2.1.5 (OFL, same metrics),
and writes `fonts/ui-fonts.css`, the Excalifont faces for the UI's hand-drawn
hints, built from each subset file's cmap.

`fonts.py` also checks every font file: its family name must match its
folder's entry in `licenses/fonts.json`, nothing may sit outside a listed
folder, and every Liberation file must be 2.x.

`tools/licenses.py` reads the list of modules the build actually contains
(`web/dist/.modules.json`, from a Vite plugin) and fails the build on any
license outside MIT, ISC, BSD, Apache-2.0, 0BSD, CC0, Zlib, OFL and similar
permissive licenses, or on a package with no license text. SPDX
expressions are parsed with their parentheses (a self-test of tricky cases
runs every build). AndroidX WebKit, the one Java library, is listed too. Packages whose
npm release has no license file get the text from `licenses/texts/`. It
writes `THIRD_PARTY_NOTICES.md` and the in-app About page.

## Android details

- `launchMode="singleTask"`, `configChanges` for every resize-related change:
  resizing or moving the window between displays never reloads the page.
- Escape always goes to the page (`dispatchKeyEvent`). Android would
  otherwise turn an unhandled Escape into Back and close the window.
- Back closes what's open first: the page reports whether a dialog, menu,
  popup, sidebar or About is open (`reportOverlay`), and while one is,
  MainActivity holds an OnBackInvokedCallback that calls
  `excalibookApp.back()`. With nothing open, Back closes the window.
- `window.confirm`/`alert` are AlertDialogs, without WebView's
  "The page at … says" heading.
- If the renderer dies (a huge drawing), the WebView is replaced and the
  drawing comes back from IndexedDB.
- Debug broadcasts (shell-only, guarded by `android.permission.DUMP`):
  devtools, reload, dump, crash, incoming.
