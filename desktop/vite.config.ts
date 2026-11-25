import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

// base "./" is required: the renderer is loaded from file:// , not from an http origin.
export default defineConfig({
  base: "./",
  plugins: [react()],
  root: fileURLToPath(new URL("./src/renderer", import.meta.url)),
  build: {
    outDir: fileURLToPath(new URL("./out/renderer", import.meta.url)),
    emptyOutDir: true,
    target: "chrome120",
    sourcemap: true
  },
  server: { host: "127.0.0.1", port: 5199, strictPort: true }
});
