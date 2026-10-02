// SPDX-License-Identifier: MIT
// The page's side of the channel to Excalibook's Android code (MainActivity),
// a WebMessageListener named `excalibook`. Outside the app (vite dev in a
// browser) there is no channel and these functions do nothing.
//
// Adapted from PDF Toolbox's page shim (shell/page_shim.js),
// copyright (c) 2026 the PDF Toolbox authors, MIT License.

import { notifySaved } from "./clean";

type Host = {
  postMessage(message: string | ArrayBuffer): void;
  addEventListener(type: "message", listener: (event: MessageEvent) => void): void;
};

declare global {
  interface Window {
    excalibook?: Host;
  }
}

const host = (): Host | undefined => window.excalibook;
export const inApp = () => !!host();

export function send(message: Record<string, unknown>) {
  host()?.postMessage(JSON.stringify(message));
}

const CHUNK = 4 << 20;

/**
 * Everything the page sends to the app as bytes (downloads, saves back to a
 * file) goes through this one queue: the app writes chunks to whichever
 * transfer is open, so two must never interleave.
 */
let outgoing: Promise<unknown> = Promise.resolve();
function enqueue<T>(job: () => Promise<T>): Promise<T> {
  const run = outgoing.then(job);
  outgoing = run.catch(() => {});
  return run;
}

async function sendBlob(blob: Blob) {
  for (let at = 0; at < blob.size; at += CHUNK) {
    host()!.postMessage(await blob.slice(at, at + CHUNK).arrayBuffer());
  }
}

// ---- Files the app hands to the page ("Open with", "Share") ----

export type Incoming = {
  name: string;
  mime: string;
  blob: Blob;
  /** Set when Android lets the app write to the file: see fileHandleFor. */
  token: string;
};
let onIncoming: (files: Incoming[]) => unknown = () => {};

/** Registers the handler and tells the app the page is ready for files. */
export function receiveFiles(handler: (files: Incoming[]) => unknown) {
  onIncoming = handler;
  send({ type: "ready" });
}

let batch: Incoming[] = [];
let current: { name: string; mime: string; token: string; chunks: ArrayBuffer[] } | null = null;
const finishCurrent = () => {
  if (current) {
    const { name, mime, token, chunks } = current;
    batch.push({ name, mime, token, blob: new Blob(chunks, { type: mime }) });
  }
  current = null;
};

function onHostMessage(event: MessageEvent) {
  const data = event.data;
  if (data instanceof ArrayBuffer) {
    current?.chunks.push(data);
    return;
  }
  let m: { type?: string; name?: string; mime?: string; token?: string; ok?: string; error?: string };
  try {
    m = JSON.parse(data);
  } catch {
    return;
  }
  switch (m.type) {
    case "incoming.begin":
      batch = [];
      current = null;
      break;
    case "incoming.file":
      finishCurrent();
      current = { name: m.name || "file", mime: m.mime || "", token: m.token || "", chunks: [] };
      break;
    case "writeback.done": {
      const pending = writes.get(m.token || "");
      writes.delete(m.token || "");
      if (m.ok === "true") pending?.resolve();
      else pending?.reject(new DOMException(m.error || "couldn't save", "NotAllowedError"));
      break;
    }
    case "incoming.end": {
      finishCurrent();
      const files = batch;
      batch = [];
      send({ type: "incoming.used", count: files.length });
      if (files.length) onIncoming(files);
      break;
    }
  }
}

// ---- Saving back to a file opened with Excalibook ----
// Excalidraw saves with browser-fs-access, which writes to an existing
// FileSystemFileHandle: getFile(), then createWritable() and the blob piped
// into it. This handle stands for a file Android handed to the app with
// write access; its writable collects the bytes and sends them to
// MainActivity, which replaces the file's contents. Only used where WebView
// has File System Access (Android 17 and later; see Web.java).

const writes = new Map<string, { resolve: () => void; reject: (e: unknown) => void }>();

function writeBack(token: string, blob: Blob): Promise<void> {
  // Queued; the next transfer starts only when the app has answered.
  return enqueue(
    () =>
      new Promise<void>((resolve, reject) => {
        writes.set(token, { resolve, reject });
        (async () => {
          send({ type: "writeback", token, size: blob.size });
          await sendBlob(blob);
          send({ type: "writeback.end", token });
        })().catch((e) => {
          writes.delete(token);
          reject(e);
        });
      }),
  );
}

export function fileHandleFor(file: Incoming): FileSystemFileHandle | null {
  if (!file.token || !host() || !("showSaveFilePicker" in window)) return null;
  let contents: Blob = file.blob;
  let broken = false;
  const handle = {
    kind: "file" as const,
    name: file.name,
    async getFile() {
      // A handle whose last save failed reports itself gone, so the next
      // save asks where to save instead.
      if (broken) throw new DOMException("The file can't be written", "NotFoundError");
      return new File([contents], file.name, { type: file.mime });
    },
    async createWritable() {
      const parts: BlobPart[] = [];
      return new WritableStream<BlobPart>({
        write(chunk) {
          parts.push(chunk);
        },
        async close() {
          const blob = new Blob(parts, { type: file.mime });
          try {
            await writeBack(file.token, blob);
            contents = blob;
            notifySaved(file.name);
          } catch (e) {
            broken = true;
            throw e;
          }
        },
      });
    },
    async queryPermission() {
      return "granted" as const;
    },
    async requestPermission() {
      return "granted" as const;
    },
    async isSameEntry(other: unknown) {
      return other === handle;
    },
  };
  return handle as unknown as FileSystemFileHandle;
}

// ---- Downloads ----
// When WebView can't show a save picker (File System Access missing), the
// save falls back to <a download href="blob:..."> and the page revokes the
// URL right away, which native code can't fetch. Keep each blob as it is
// created and stream its bytes to the app, which writes them to Download/.

const blobs = new Map<string, Blob>();

function installDownloads() {
  const createURL = URL.createObjectURL;
  const revokeURL = URL.revokeObjectURL;
  URL.createObjectURL = function (obj: Blob | MediaSource) {
    const url = createURL.call(URL, obj);
    if (obj instanceof Blob) blobs.set(url, obj);
    return url;
  };
  URL.revokeObjectURL = function (url: string) {
    setTimeout(() => blobs.delete(url), 60000);
    return revokeURL.call(URL, url);
  };
  const saveLink = (a: HTMLAnchorElement) => {
    if (!a.hasAttribute("download")) return false;
    const blob = blobs.get(a.href);
    if (!blob) return false;
    const name = a.getAttribute("download") || "download";
    enqueue(async () => {
      send({ type: "download", name, mime: blob.type, size: blob.size });
      await sendBlob(blob);
      send({ type: "download.end" });
      notifySaved(name); // a drawing saved to Download (Android 16 and earlier)
    }).catch((e) => send({ type: "download.failed", name, error: String(e) }));
    return true;
  };
  const click = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function () {
    if (!saveLink(this)) return click.call(this);
  };
  document.addEventListener(
    "click",
    (event) => {
      const a = event.target instanceof Element && event.target.closest("a[download]");
      if (a && saveLink(a as HTMLAnchorElement)) event.preventDefault();
    },
    true,
  );
}

// ---- Back ----

let lastOverlay = false;
/** Whether something is open that Back should close (a dialog, menu, sidebar). */
export function reportOverlay(open: boolean) {
  if (open === lastOverlay) return;
  lastOverlay = open;
  send({ type: "overlay", open });
}

// ---- Window colors ----

let lastColor = "";
/** The caption bar takes the color of the canvas behind it. */
export function reportColor(color: string) {
  if (color === lastColor) return;
  lastColor = color;
  send({ type: "theme", color });
}

if (host()) {
  host()!.addEventListener("message", onHostMessage);
  installDownloads();
  // No service worker: the files are local already.
  if (navigator.serviceWorker) {
    try {
      Object.defineProperty(navigator.serviceWorker, "register", {
        value: () => Promise.reject(new DOMException("Not needed in Excalibook", "NotSupportedError")),
        configurable: true,
      });
    } catch {
      /* leave it */
    }
  }
}
