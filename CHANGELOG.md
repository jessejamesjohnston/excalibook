# Changelog

## 0.1.0 (unreleased)

First version: Excalidraw 0.18.1's editor as an offline Googlebook app.

- The whole editor: shapes, arrows, text, free draw, images, frames, the
  laser pointer, the library, Mermaid to Excalidraw, light and dark themes,
  every keyboard shortcut.
- The drawing and library are kept in the app as you work.
- Open, save and export (PNG, SVG, clipboard). On Android 17 and later,
  Ctrl+S saves back to the file you opened or saved, including files opened
  from Files with Open with. Earlier versions save to Download.
- Open with for `.excalidraw` and `.excalidrawlib` files; images shared to
  the app go onto the canvas.
- No internet permission. Collaboration, share links, the online library
  browser, web embeds and the AI tools are left out.
- Saving is careful with your work: the drawing and its images are kept in
  one step, a failed save keeps retrying, a drawing that can't be reopened is
  set aside rather than overwritten, and saving back to a file restores the
  file if the write fails.
- Back closes an open menu, dialog or sidebar before the window.
- Dark mode follows the system unless you picked the other theme yourself.
- Several images shared at once are laid out so none hides another.
- Every bundled part is permissively licensed (About Excalibook lists them);
  Liberation Sans 2.1.5 (OFL) replaces Excalidraw's GPL 1.05 copy.
