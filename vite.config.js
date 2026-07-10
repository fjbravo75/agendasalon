import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";


export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "static/react",
    emptyOutDir: true,
    cssCodeSplit: false,
    rollupOptions: {
      input: resolve("frontend/agenda/main.jsx"),
      output: {
        entryFileNames: "agenda.js",
        chunkFileNames: "chunks/[name]-[hash].js",
        assetFileNames: "agenda.[ext]",
      },
    },
  },
});
