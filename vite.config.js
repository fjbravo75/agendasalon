import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";


export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    include: ["frontend/**/*.test.jsx"],
    setupFiles: ["./frontend/test-setup.js"],
    restoreMocks: true,
  },
  build: {
    outDir: "static/react",
    emptyOutDir: true,
    cssCodeSplit: true,
    rollupOptions: {
      input: {
        agenda: resolve("frontend/agenda/main.jsx"),
        dashboard: resolve("frontend/dashboard/main.jsx"),
      },
      output: {
        entryFileNames: "[name].js",
        chunkFileNames: "chunks/[name]-[hash].js",
        assetFileNames: "[name].[ext]",
      },
    },
  },
});
