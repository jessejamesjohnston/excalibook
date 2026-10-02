// SPDX-License-Identifier: MIT
// The drawing on the canvas, its images and the library, kept in the app's
// IndexedDB so closing Excalibook (or Android stopping it) never loses work.
// Saving to a file is separate: that's the Save and Export commands.
//
// Every save of the drawing is one transaction over the scene and its images,
// so a restart sees the old drawing or the new one, never a scene whose images
// didn't make it. Saves run one at a time, in order.
import {
  restore,
  restoreLibraryItems,
  serializeAsJSON,
} from "@excalidraw/excalidraw";
import type {
  AppState,
  BinaryFileData,
  BinaryFiles,
  ExcalidrawInitialDataState,
  LibraryItems,
} from "@excalidraw/excalidraw/types";
import type { OrderedExcalidrawElement } from "@excalidraw/excalidraw/element/types";

const DB_NAME = "excalibook";
const KV = "kv";
const FILES = "files";

let dbPromise: Promise<IDBDatabase> | null = null;

function db(): Promise<IDBDatabase> {
  if (!dbPromise) {
    dbPromise = new Promise<IDBDatabase>((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => {
        req.result.createObjectStore(KV);
        req.result.createObjectStore(FILES);
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    }).catch((e) => {
      dbPromise = null; // the next save tries to open it again
      throw e;
    });
  }
  return dbPromise;
}

function done(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onabort = tx.onerror = () => reject(tx.error ?? new DOMException("transaction aborted", "AbortError"));
  });
}

async function get<T>(store: string, key: string): Promise<T | undefined> {
  const tx = (await db()).transaction(store, "readonly");
  const req = tx.objectStore(store).get(key);
  await done(tx);
  return req.result as T | undefined;
}

async function put(store: string, key: string, value: unknown): Promise<void> {
  const tx = (await db()).transaction(store, "readwrite");
  tx.objectStore(store).put(value, key);
  await done(tx);
}

const imageIds = (elements: readonly { type: string; isDeleted?: boolean; fileId?: string | null }[]) =>
  new Set(elements.filter((e) => e.type === "image" && e.fileId).map((e) => e.fileId as string));

/**
 * The drawing as it was when the app last saved it, or null the first time.
 * Throws if there is a saved drawing that couldn't be read: the caller must
 * then keep it (see keepUnreadable) rather than save over it.
 */
export async function loadScene(): Promise<ExcalidrawInitialDataState | null> {
  const json = await get<string>(KV, "scene");
  if (!json) return null;
  const data = JSON.parse(json);
  const restored = restore(data, null, null, { repairBindings: true });
  // Only the images the drawing uses (deleted elements included, for undo).
  const files: BinaryFiles = {};
  const tx = (await db()).transaction(FILES, "readonly");
  const store = tx.objectStore(FILES);
  const reqs = [...imageIds(data.elements ?? [])].map((id) => store.get(id));
  await done(tx);
  for (const r of reqs) {
    const f = r.result as BinaryFileData | undefined;
    if (f) files[f.id] = f;
  }
  lastJson = json;
  for (const f of Object.values(files)) saved.set(f.id, f.dataURL);
  return { ...restored, files };
}

/**
 * Moves a saved drawing that couldn't be restored out of the way, under a
 * dated key, so the new drawing's saves don't overwrite it. Returns the key.
 */
export async function keepUnreadable(): Promise<string | null> {
  const json = await get<string>(KV, "scene");
  if (!json) return null;
  const key = `scene-unreadable-${new Date().toISOString()}`;
  const tx = (await db()).transaction(KV, "readwrite");
  tx.objectStore(KV).put(json, key);
  tx.objectStore(KV).delete("scene");
  await done(tx);
  return key;
}

/** The last committed scene JSON and, per image, the bytes committed for it. */
let lastJson = "";
const saved = new Map<string, string>();
let chain: Promise<void> = Promise.resolve();

/**
 * Saves the drawing and the images it uses in one transaction, and drops
 * images nothing uses any more. Queued behind any save still running.
 */
export function saveScene(
  elements: readonly OrderedExcalidrawElement[],
  appState: AppState,
  files: BinaryFiles,
): Promise<void> {
  const run = chain.then(() => commit(elements, appState, files));
  chain = run.catch(() => {});
  return run;
}

async function commit(elements: readonly OrderedExcalidrawElement[], appState: AppState, files: BinaryFiles) {
  const json = serializeAsJSON(elements, appState, {}, "local");
  const used = imageIds(elements);
  const toPut = [...used].filter((id) => files[id] && saved.get(id) !== files[id].dataURL);
  const toDrop = [...saved.keys()].filter((id) => !used.has(id));
  if (json === lastJson && !toPut.length && !toDrop.length) return;
  const tx = (await db()).transaction([KV, FILES], "readwrite");
  if (json !== lastJson) tx.objectStore(KV).put(json, "scene");
  for (const id of toPut) tx.objectStore(FILES).put(files[id], id);
  for (const id of toDrop) tx.objectStore(FILES).delete(id);
  await done(tx);
  lastJson = json;
  for (const id of toPut) saved.set(id, files[id].dataURL);
  for (const id of toDrop) saved.delete(id);
}

/** Images saved by app versions that didn't clean up: dropped once at startup. */
export async function dropUnusedImages(): Promise<void> {
  const keep = new Set(saved.keys());
  const tx = (await db()).transaction(FILES, "readwrite");
  const req = tx.objectStore(FILES).getAllKeys();
  req.onsuccess = () => {
    for (const k of req.result) if (!keep.has(String(k))) tx.objectStore(FILES).delete(k);
  };
  await done(tx);
}

/** The fingerprint of the drawing as last opened from or saved to a file (clean.ts). */
export async function loadClean(): Promise<string | null> {
  return (await get<string>(KV, "clean")) ?? null;
}

export async function saveClean(value: string): Promise<void> {
  await put(KV, "clean", value);
}

/** Excalidraw's library (the sidebar of reusable shapes). */
export const libraryAdapter = {
  async load(): Promise<{ libraryItems: LibraryItems } | null> {
    const items = await get<LibraryItems>(KV, "library");
    return items ? { libraryItems: restoreLibraryItems(items, "unpublished") } : null;
  },
  async save({ libraryItems }: { libraryItems: LibraryItems }): Promise<void> {
    await put(KV, "library", libraryItems);
  },
};
