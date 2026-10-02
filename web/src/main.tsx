// SPDX-License-Identifier: MIT
import { createRoot } from "react-dom/client";
import "@excalidraw/excalidraw/index.css";
import "./app.css";
import { App } from "./App";

createRoot(document.getElementById("root")!).render(<App />);
