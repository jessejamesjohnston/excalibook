// SPDX-License-Identifier: MIT
// Excalibook: Excalidraw's editor, offline, as a Googlebook app.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CaptureUpdateAction,
  Excalidraw,
  MIME_TYPES,
  serializeAsJSON,
  MainMenu,
  WelcomeScreen,
  convertToExcalidrawElements,
  getDataURL,
  loadSceneOrLibraryFromBlob,
  useHandleLibrary,
} from "@excalidraw/excalidraw";
import type {
  AppState,
  BinaryFileData,
  BinaryFiles,
  DataURL,
  ExcalidrawImperativeAPI,
} from "@excalidraw/excalidraw/types";
import type { FileId, OrderedExcalidrawElement } from "@excalidraw/excalidraw/element/types";
import * as excalidrawLib from "@excalidraw/excalidraw";
import { dropUnusedImages, keepUnreadable, libraryAdapter, loadClean, loadScene, saveClean, saveScene } from "./storage";
import { fingerprint, watchSaves } from "./clean";
import { fileHandleFor, receiveFiles, reportColor, reportOverlay, type Incoming } from "./android";

const SAVE_DELAY = 300;

declare global {
  interface Window {
    /** For tests over DevTools and for MainActivity.onPause. */
    excalibookApp?: {
      api: ExcalidrawImperativeAPI | null;
      flush: () => Promise<void>;
      /** Back: closes what's open (MainActivity calls it while reportOverlay says so). */
      back: () => void;
      version: string;
      lib: typeof excalidrawLib;
    };
  }
}

const isDark = () => window.matchMedia("(prefers-color-scheme: dark)").matches;
const RETRY_DELAY = 5000;

type Snapshot = [readonly OrderedExcalidrawElement[], AppState, BinaryFiles];

/**
 * Saves the drawing 300 ms after each change and when the page is hidden.
 * Nothing is saved until `enable()` (after the stored drawing is restored),
 * and a failed save keeps its snapshot and tries again.
 */
function useAutosave(onFailure: (e: unknown) => void) {
  const pending = useRef<Snapshot | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const inflight = useRef<Promise<void>>(Promise.resolve());
  const enabled = useRef(false);
  const failing = useRef(false);
  const flush = useCallback(async (): Promise<void> => {
    window.clearTimeout(timer.current);
    const p = pending.current;
    if (!enabled.current || !p) return inflight.current;
    pending.current = null;
    const run = saveScene(...p).then(
      () => {
        failing.current = false;
      },
      (e) => {
        console.error("autosave failed", e);
        if (!pending.current) pending.current = p; // keep it unless something newer came
        if (!failing.current) onFailure(e);
        failing.current = true;
        timer.current = window.setTimeout(() => void flush(), RETRY_DELAY);
      },
    );
    inflight.current = run;
    return run;
  }, [onFailure]);
  const schedule = useCallback(
    (snapshot: Snapshot) => {
      pending.current = snapshot;
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => void flush(), SAVE_DELAY);
    },
    [flush],
  );
  const enable = useCallback(() => {
    enabled.current = true;
  }, []);
  useEffect(() => {
    const onHide = () => {
      if (document.visibilityState === "hidden") void flush();
    };
    document.addEventListener("visibilitychange", onHide);
    window.addEventListener("pagehide", onHide);
    return () => {
      document.removeEventListener("visibilitychange", onHide);
      window.removeEventListener("pagehide", onHide);
    };
  }, [flush]);
  return { schedule, flush, enable };
}

const baseName = (name: string) => name.replace(/\.(excalidraw|json|png|svg)$/i, "").replace(/\.excalidraw$/i, "");

async function imageSize(dataURL: string): Promise<{ width: number; height: number }> {
  const img = new Image();
  img.src = dataURL;
  await img.decode();
  return { width: img.naturalWidth, height: img.naturalHeight };
}

/** Files larger than this are refused: the page holds a whole file in memory. */
const MAX_FILE = 100 << 20;

export function App() {
  const [api, setApi] = useState<ExcalidrawImperativeAPI | null>(null);
  const apiRef = useRef<ExcalidrawImperativeAPI | null>(null);
  apiRef.current = api;
  const [about, setAbout] = useState(false);
  const toast = useCallback((message: string) => {
    apiRef.current?.setToast({ message, closable: true, duration: 10000 });
  }, []);
  const onSaveFailure = useCallback(
    () => toast("Excalibook couldn't keep your drawing on this device (is storage full?). It keeps trying; to be safe, save it to a file with Ctrl+S."),
    [toast],
  );
  const { schedule, flush, enable } = useAutosave(onSaveFailure);
  // The canvas's fingerprint when it was last opened from or saved to a
  // file. Opening another file asks first only if the canvas differs.
  const clean = useRef<string | null>(null);
  const markClean = useCallback(() => {
    const a = apiRef.current;
    if (!a) return;
    clean.current = fingerprint(a.getSceneElements());
    void saveClean(clean.current).catch(() => {});
  }, []);
  const isDirty = useCallback(() => {
    const a = apiRef.current;
    if (!a) return false;
    const els = a.getSceneElements();
    return els.length > 0 && fingerprint(els) !== clean.current;
  }, []);
  useEffect(() => watchSaves(markClean), [markClean]);
  const lastHandle = useRef<unknown>(null);

  // "loading" until the stored drawing is on the canvas; files opened with
  // Excalibook wait for "ready" so the restored drawing can't replace them.
  const [restored, setRestored] = useState<"loading" | "ready" | "failed">("loading");

  const initialData = useMemo(() => {
    const blank = () => ({ appState: { theme: isDark() ? ("dark" as const) : ("light" as const) } });
    return loadScene()
      .then(async (scene) => {
        clean.current = await loadClean().catch(() => null);
        enable();
        setRestored("ready");
        void dropUnusedImages().catch(() => {});
        return scene ?? blank();
      })
      .catch(async (e) => {
        console.error("couldn't restore the drawing", e);
        // Keep the unreadable drawing under another key before anything is
        // saved; if even that fails, don't save at all this session.
        try {
          await keepUnreadable();
          enable();
          setRestored("failed");
        } catch (e2) {
          console.error("couldn't set the unreadable drawing aside", e2);
          setRestored("failed");
        }
        return blank();
      });
  }, [enable]);

  useEffect(() => {
    if (api && restored === "failed") {
      toast("Excalibook couldn't reopen your last drawing, so this is a new one. The old one is kept in the app's storage, not deleted.");
    }
  }, [api, restored, toast]);

  useHandleLibrary({ excalidrawAPI: api, adapter: libraryAdapter, validateLibraryUrl: () => false });

  useEffect(() => {
    const back = () => {
      if (aboutOpen.current) {
        setAbout(false);
        return;
      }
      api?.updateScene({
        appState: { openDialog: null, openMenu: null, openPopup: null, openSidebar: null, contextMenu: null, showHyperlinkPopup: false },
      });
    };
    window.excalibookApp = { api, flush, back, version: __APP_VERSION__, lib: excalidrawLib };
  }, [api, flush]);

  // Back closes a dialog, menu or sidebar before it closes the window.
  const aboutOpen = useRef(false);
  const overlay = useRef({ scene: false });
  useEffect(() => {
    aboutOpen.current = about;
    reportOverlay(about || overlay.current.scene);
  }, [about]);

  // Files opened with or shared to Excalibook, one batch at a time, in order.
  const openFiles = useCallback(
    async (files: Incoming[]) => {
      const api = apiRef.current;
      if (!api) return;
      for (const f of files) {
        if (f.blob.size > MAX_FILE) {
          toast(`${f.name} is too large to open in Excalibook (over ${MAX_FILE >> 20} MB).`);
          continue;
        }
        try {
          const isImage = f.mime.startsWith("image/") || /\.(png|jpe?g|gif|webp|svg)$/i.test(f.name);
          const mayHoldDrawing = /png|svg/.test(f.mime) || /\.(png|svg)$/i.test(f.name);
          if (isImage && !mayHoldDrawing) {
            await insertImage(api, f);
            continue;
          }
          let loaded;
          try {
            loaded = await loadSceneOrLibraryFromBlob(
              new File([f.blob], f.name, { type: f.mime }),
              api.getAppState(),
              api.getSceneElements(),
            );
          } catch (e) {
            // A PNG or SVG without a drawing in it is a plain image.
            if (isImage) {
              await insertImage(api, f);
              continue;
            }
            throw e;
          }
          if (loaded.type === "application/vnd.excalidrawlib+json") {
            await api.updateLibrary({ libraryItems: loaded.data.libraryItems ?? [], merge: true, openLibraryMenu: true });
            continue;
          }
          // Only ask when replacing the canvas would lose changes.
          const current = api.getAppState();
          const sameFile = baseName(f.name) === (current.name || baseName((current.fileHandle as { name?: string } | null)?.name ?? ""));
          const question = sameFile
            ? `Reopen "${f.name}"? The changes you made since you last saved it will be lost.`
            : `Open "${f.name}"? The drawing on the canvas has changes that aren't saved. Save it first if you want to keep them.`;
          if (isDirty() && !window.confirm(question)) {
            continue;
          }
          const { elements, appState, files: binaries } = loaded.data;
          if (binaries) api.addFiles(Object.values(binaries));
          api.updateScene({
            elements,
            // Ctrl+S saves back to the file when Android allows it (else Save asks where).
            appState: { ...appState, name: baseName(f.name), fileHandle: fileHandleFor(f) },
            captureUpdate: CaptureUpdateAction.IMMEDIATELY,
          });
          api.scrollToContent(elements, { fitToContent: true, animate: false });
          markClean();
        } catch (e) {
          console.error("couldn't open", f.name, e);
          api.setToast({ message: `Couldn't open ${f.name}: it isn't an Excalidraw drawing, library or image.`, closable: true, duration: 6000 });
        }
      }
    },
    [toast, isDirty, markClean],
  );

  const queue = useRef<Promise<void>>(Promise.resolve());
  useEffect(() => {
    if (!api || restored === "loading") return;
    receiveFiles((files) => {
      const run = queue.current.then(() => openFiles(files));
      queue.current = run.catch(() => {});
      return run;
    });
  }, [api, restored, openFiles]);

  // Ctrl+Shift+S: Save as (see saveAs).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // `code` can be empty in WebView, so the key too.
      if (e.ctrlKey && e.shiftKey && !e.altKey && (e.code === "KeyS" || e.key.toLowerCase() === "s")) {
        e.preventDefault();
        e.stopPropagation();
        void saveAs(apiRef.current);
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);

  // Dark mode follows the system while the drawing's theme matches it: a
  // drawing you switched to the other theme yourself stays as you set it.
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (e: MediaQueryListEvent) => {
      const a = apiRef.current;
      const was = e.matches ? "light" : "dark";
      if (a && a.getAppState().theme === was) a.updateScene({ appState: { theme: e.matches ? "dark" : "light" } });
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const onChange = useCallback(
    (elements: readonly OrderedExcalidrawElement[], appState: AppState, files: BinaryFiles) => {
      schedule([elements, appState, files]);
      // A drawing Excalidraw opened (Ctrl+O) or saved to a new file comes with a
      // new file handle: the canvas now matches that file.
      if (appState.fileHandle && appState.fileHandle !== lastHandle.current) {
        clean.current = fingerprint(elements);
        void saveClean(clean.current).catch(() => {});
      }
      lastHandle.current = appState.fileHandle;
      overlay.current.scene = !!(appState.openDialog || appState.openMenu || appState.openPopup || appState.openSidebar
        || appState.contextMenu || appState.showHyperlinkPopup);
      reportOverlay(aboutOpen.current || overlay.current.scene);
      reportColor(appState.theme === "dark" ? "#121212" : /^#[0-9a-f]{6}$/i.test(appState.viewBackgroundColor) ? appState.viewBackgroundColor : "#ffffff");
      // A drawing opened with Ctrl+O has no name of its own, only its file's.
      const name = appState.name || (appState.fileHandle?.name ? baseName(appState.fileHandle.name) : "");
      const title = name ? `${name} – Excalibook` : "Excalibook";
      if (document.title !== title) document.title = title;
    },
    [schedule],
  );

  return (
    <>
      <Excalidraw
        excalidrawAPI={setApi}
        initialData={initialData}
        onChange={onChange}
        aiEnabled={false}
        validateEmbeddable={false}
        handleKeyboardGlobally
        autoFocus
        UIOptions={{ canvasActions: { export: { saveFileToDisk: true } } }}
      >
        <MainMenu>
          <MainMenu.DefaultItems.LoadScene />
          <MainMenu.DefaultItems.SaveToActiveFile />
          {/* Excalidraw's "Save to..." opens a dialog whose only option left
              (the cloud ones are gone) is "Save to disk": go straight to the picker. */}
          <MainMenu.Item onSelect={() => void saveAs(api)} icon={saveIcon} shortcut="Ctrl+Shift+S">
            Save as...
          </MainMenu.Item>
          <MainMenu.DefaultItems.SaveAsImage />
          <MainMenu.DefaultItems.CommandPalette />
          <MainMenu.DefaultItems.SearchMenu />
          <MainMenu.DefaultItems.Help />
          <MainMenu.DefaultItems.ClearCanvas />
          <MainMenu.Separator />
          <MainMenu.Item onSelect={() => setAbout(true)} icon={infoIcon}>
            About Excalibook
          </MainMenu.Item>
          <MainMenu.Separator />
          <MainMenu.DefaultItems.ToggleTheme />
          <MainMenu.DefaultItems.ChangeCanvasBackground />
        </MainMenu>
        <WelcomeScreen>
          <WelcomeScreen.Hints.MenuHint />
          <WelcomeScreen.Hints.ToolbarHint />
          <WelcomeScreen.Hints.HelpHint />
          <WelcomeScreen.Center>
            <WelcomeScreen.Center.Logo>
              <span className="xb-logo">Excalibook</span>
            </WelcomeScreen.Center.Logo>
            <WelcomeScreen.Center.Heading>
              Sketch and diagram on your Googlebook. Everything stays on this device.
            </WelcomeScreen.Center.Heading>
            <WelcomeScreen.Center.Menu>
              <WelcomeScreen.Center.MenuItemLoadScene />
              <WelcomeScreen.Center.MenuItemHelp />
            </WelcomeScreen.Center.Menu>
          </WelcomeScreen.Center>
        </WelcomeScreen>
      </Excalidraw>
      {about && <About onClose={() => setAbout(false)} />}
    </>
  );
}

/** Images that arrive within a few seconds of each other step down and right, so none hides another. */
let burst = { count: 0, at: 0 };

/** Puts an image at the middle of the view. */
async function insertImage(api: ExcalidrawImperativeAPI, f: Incoming) {
  const now = Date.now();
  const nth = now - burst.at < 3000 ? burst.count : 0;
  burst = { count: nth + 1, at: now };
  const dataURL = (await getDataURL(f.blob)) as DataURL;
  const id = (await sha1(f.blob)) as FileId;
  const file: BinaryFileData = { id, dataURL, mimeType: f.mime as BinaryFileData["mimeType"], created: Date.now() };
  api.addFiles([file]);
  const size = await imageSize(dataURL);
  const scale = Math.min(1, 800 / Math.max(size.width, size.height));
  const { scrollX, scrollY, width, height, zoom } = api.getAppState();
  const w = size.width * scale;
  const h = size.height * scale;
  const step = 40 * nth;
  const x = width / 2 / zoom.value - scrollX - w / 2 + step;
  const y = height / 2 / zoom.value - scrollY - h / 2 + step;
  const [image] = convertToExcalidrawElements([{ type: "image", fileId: id, x, y, width: w, height: h }]);
  api.updateScene({
    elements: [...api.getSceneElementsIncludingDeleted(), image],
    captureUpdate: CaptureUpdateAction.IMMEDIATELY,
  });
}

async function sha1(blob: Blob): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-1", await blob.arrayBuffer());
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function About({ onClose }: { onClose: () => void }) {
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    close.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      before?.focus?.();
    };
  }, [onClose]);
  // Escape inside the license page (same origin) closes too.
  const onFrameLoad = (e: React.SyntheticEvent<HTMLIFrameElement>) => {
    e.currentTarget.contentWindow?.addEventListener("keydown", (k) => {
      if (k.key === "Escape") onClose();
    });
  };
  // Keys typed in the dialog stay in it: Excalidraw's shortcuts listen on the document.
  const keep = (e: React.KeyboardEvent) => {
    if (e.key !== "Escape") e.stopPropagation();
  };
  return (
    <div className="xb-about" onClick={onClose} onKeyDown={keep}>
      <div className="xb-about__panel" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="About Excalibook">
        <div className="xb-about__bar">
          <span>About</span>
          <button ref={close} onClick={onClose}>
            Close
          </button>
        </div>
        <iframe src="/about.html" title="About Excalibook and licenses" onLoad={onFrameLoad} />
      </div>
    </div>
  );
}

/**
 * Save as: the save picker (starting in Download, the name filled in), then
 * the drawing linked to the new file so Ctrl+S saves there. Excalidraw has
 * this action, but in 0.18.1 neither its menu (a dialog with one button left)
 * nor its Ctrl+Shift+S (the key test misses the shifted "S") reaches it.
 * Called from a click or key press: the picker needs that user activation,
 * so it is opened before anything is awaited.
 */
async function saveAs(api: ExcalidrawImperativeAPI | null) {
  if (!api) return;
  const appState = api.getAppState();
  const name = appState.name || "Untitled";
  const json = serializeAsJSON(api.getSceneElements(), appState, api.getFiles(), "local");
  const blob = new Blob([json], { type: MIME_TYPES.excalidraw });
  if (!("showSaveFilePicker" in window)) {
    // Android 16 and earlier: no save picker; it goes to Download.
    const a = document.createElement("a");
    a.download = `${name}.excalidraw`;
    a.href = URL.createObjectURL(blob);
    a.click();
    URL.revokeObjectURL(a.href);
    return;
  }
  try {
    const handle = await (window as unknown as {
      showSaveFilePicker(o: object): Promise<FileSystemFileHandle>;
    }).showSaveFilePicker({
      suggestedName: `${name}.excalidraw`,
      types: [{ description: "Excalidraw file", accept: { [MIME_TYPES.excalidraw]: [".excalidraw"] } }],
    });
    const writable = await handle.createWritable();
    await blob.stream().pipeTo(writable as unknown as WritableStream);
    api.updateScene({ appState: { fileHandle: handle, name: baseName(handle.name) } });
    api.setToast({ message: `Saved to "${handle.name}"`, duration: 3000 });
  } catch (e) {
    if ((e as Error)?.name === "AbortError") return; // the picker was cancelled
    console.error("save as failed", e);
    api.setToast({ message: "Couldn't save the drawing.", closable: true, duration: 8000 });
  }
}

const saveIcon = (
  <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19h14" />
  </svg>
);

const infoIcon = (
  <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <circle cx="12" cy="12" r="9" />
    <path d="M12 11v5M12 8h.01" />
  </svg>
);
