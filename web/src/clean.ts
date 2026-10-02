// SPDX-License-Identifier: MIT
// Whether the canvas has changes that aren't in a file. "Clean" is recorded
// when a drawing is opened from a file or saved to one; opening another file
// only asks before replacing the canvas when it isn't clean.
//
// The fingerprint is the drawing's content: what you drew, where, and how it
// looks. It leaves out what Excalidraw recomputes by itself (version
// counters, and text sizes and the positions of labels inside shapes, which
// change when fonts finish loading), so a drawing nobody touched stays clean.
import type { OrderedExcalidrawElement } from "@excalidraw/excalidraw/element/types";

type El = OrderedExcalidrawElement & Record<string, unknown>;

const round = (n: unknown) => (typeof n === "number" ? Math.round(n * 100) / 100 : n);

export function fingerprint(elements: readonly OrderedExcalidrawElement[]): string {
  const parts = (elements as readonly El[])
    .filter((e) => !e.isDeleted)
    .map((e) => {
      const text = e.type === "text";
      const label = text && !!e.containerId;
      return [
        e.id, e.type, label ? null : round(e.x), label ? null : round(e.y),
        text ? null : round(e.width), text ? null : round(e.height), round(e.angle),
        e.strokeColor, e.backgroundColor, e.fillStyle, e.strokeWidth, e.strokeStyle, e.roughness, e.opacity,
        e.roundness ? (e.roundness as { type: number }).type : null, e.groupIds, e.frameId, e.link, e.locked,
        text ? e.originalText ?? e.text : null, text ? e.fontSize : null, text ? e.fontFamily : null,
        text ? e.textAlign : null, e.containerId ?? null, e.fileId ?? null,
        Array.isArray(e.points) ? (e.points as number[][]).map((p) => p.map(round)) : null,
        (e as { startBinding?: { elementId: string } }).startBinding?.elementId ?? null,
        (e as { endBinding?: { elementId: string } }).endBinding?.elementId ?? null,
        e.startArrowhead ?? null, e.endArrowhead ?? null, e.index,
      ];
    });
  // FNV-1a over the JSON: a short, stable value to store.
  let h = 0x811c9dc5;
  const s = JSON.stringify(parts);
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return `${parts.length}:${h.toString(16)}`;
}

let saved: (() => void) | null = null;

/** Calls `onSaved` after a drawing (.excalidraw) has been written to a file. */
export function watchSaves(onSaved: () => void) {
  saved = onSaved;
}

export function notifySaved(name: string) {
  if (/\.excalidraw$/i.test(name)) saved?.();
}

// Saves through File System Access (Android 17+): the library pipes the
// drawing into the handle's writable stream. Wrap that stream so its close,
// which only resolves once the file is written, marks the canvas clean.
// (Writes go to the real stream unchanged.)
const proto = (globalThis as { FileSystemFileHandle?: { prototype: FileSystemFileHandle } }).FileSystemFileHandle?.prototype;
if (proto && typeof proto.createWritable === "function") {
  const createWritable = proto.createWritable;
  proto.createWritable = async function (this: FileSystemFileHandle, ...args: Parameters<FileSystemFileHandle["createWritable"]>) {
    const real = await createWritable.apply(this, args);
    if (!/\.excalidraw$/i.test(this.name)) return real;
    const name = this.name;
    const wrapped = new WritableStream({
      write: (chunk) => real.write(chunk),
      close: async () => {
        await real.close();
        notifySaved(name);
      },
      abort: (reason) => real.abort(reason),
    });
    return wrapped as unknown as FileSystemWritableFileStream;
  };
}
