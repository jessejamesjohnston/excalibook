// SPDX-License-Identifier: MIT
// Builds Excalibook's page: Excalidraw's React component, our app around it,
// and a few patches to Excalidraw's published build (below). The build fails
// if a patch doesn't match exactly as often as expected, so an Excalidraw
// update can't silently bring a network feature back.
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

type Patch = { name: string; find: string | RegExp; replace: string; count: number };

// Pinned to @excalidraw/excalidraw 0.18.1 (package.json). Minified names
// differ between releases; after an update, fix the patterns, never the counts.
const PATCHES: Patch[] = [
  {
    // Fonts load from the app. Excalidraw adds esm.sh as a last fallback for
    // every font; make that fallback the app's own origin too.
    name: "fonts: no CDN fallback",
    find: /`https:\/\/esm\.sh\/\$\{[^`]*`[^`]*`[^}]*\}\/dist\/prod\/`/g,
    replace: '(window.location.origin+"/")',
    count: 1,
  },
  {
    // "Publish selected" uploads library items to Excalidraw's servers.
    name: "library: no Publish item",
    find: /(\w+)&&(\w+\(\w+\.Item,\{icon:[\w$]+,onSelect:\(\)=>\w+\(!0\),"data-testid":"lib-dropdown--remove",children:\w+\("buttons\.publishLibrary"\)\}\))/g,
    replace: "!1&&$2",
    count: 1,
  },
  {
    // "Browse libraries" opens libraries.excalidraw.com, which hands the
    // library back through a URL this app can't receive.
    name: "library: no Browse link",
    find: /return (\w+)\("a",\{className:"library-menu-browse-button"/g,
    replace: 'return null;$1("a",{className:"library-menu-browse-button"',
    count: 1,
  },
  {
    // Web embeds (YouTube, Figma...) need the internet. The tool's menu item
    // goes; pasted links stay links (validateEmbeddable={false} in App.tsx).
    // Not CSS: upstream gives "Mermaid to Excalidraw" the same data-testid.
    name: "toolbar: no Web Embed item",
    find: /[\w$]+\([\w$]+\.Item,\{onSelect:\(\)=>[\w$]+\.setActiveTool\(\{type:"embeddable"\}\),icon:[\w$]+,"data-testid":"toolbar-embeddable",selected:[\w$]+,children:[\w$]+\("toolBar\.embeddable"\)\}\),/g,
    replace: "",
    count: 1,
  },
  {
    // The empty library's hint points at the Browse link removed above.
    // (English only; other languages keep Excalidraw's wording.)
    name: "library: empty-state hint",
    find: 'hint_emptyLibrary:"Select an item on canvas to add it here, or install a library from the public repository, below."',
    replace: 'hint_emptyLibrary:"Select an item on the canvas to add it here, or open a .excalidrawlib file with the menu below."',
    count: 1,
  },
];

function patchExcalidraw(): Plugin {
  const hits = new Map<string, number>(PATCHES.map((p) => [p.name, 0]));
  return {
    name: "excalibook-patch-excalidraw",
    enforce: "pre",
    transform(code, id) {
      if (!/@excalidraw[\\/]excalidraw[\\/]dist[\\/]prod[\\/].*\.js$/.test(id)) return null;
      let out = code;
      for (const p of PATCHES) {
        const find = typeof p.find === "string" ? new RegExp(p.find.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g") : p.find;
        out = out.replace(find, (...m) => {
          hits.set(p.name, (hits.get(p.name) ?? 0) + 1);
          const groups = m.slice(1, -2);
          return p.replace.replace(/\$(\d)/g, (_, i) => groups[Number(i) - 1] ?? "");
        });
      }
      return out === code ? null : { code: out, map: null };
    },
    buildEnd(error) {
      if (error) return;
      const wrong = PATCHES.filter((p) => hits.get(p.name) !== p.count)
        .map((p) => `${p.name}: matched ${hits.get(p.name)}x, expected ${p.count}x`);
      if (wrong.length) this.error(`Excalidraw patches didn't apply:\n  ${wrong.join("\n  ")}`);
    },
  };
}

// Every package the page actually contains, for the license gate
// (tools/licenses.py reads dist/.modules.json).
function moduleList(): Plugin {
  return {
    name: "excalibook-module-list",
    generateBundle(_, bundle) {
      const ids = new Set<string>();
      for (const chunk of Object.values(bundle)) {
        if (chunk.type === "chunk") for (const id of Object.keys(chunk.modules)) ids.add(id);
      }
      this.emitFile({ type: "asset", fileName: ".modules.json", source: JSON.stringify([...ids].sort(), null, 1) });
    },
  };
}

export default defineConfig({
  plugins: [patchExcalidraw(), react(), moduleList()],
  define: {
    "process.env.IS_PREACT": JSON.stringify("false"),
    __APP_VERSION__: JSON.stringify(process.env.XB_VERSION ?? "dev"),
  },
  build: {
    target: "chrome120",
    sourcemap: false,
    chunkSizeWarningLimit: 4096,
    assetsInlineLimit: 0,
  },
});
