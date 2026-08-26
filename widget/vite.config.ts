import { defineConfig } from "vite";
import { resolve } from "path";

export default defineConfig({
  build: {
    lib: {
      entry: resolve(__dirname, "src/index.ts"),
      name: "ClinicalRagChatbot",
      formats: ["iife"],
      fileName: () => "chatbot.js",
    },
    outDir: "dist",
    emptyOutDir: true,
    cssCodeSplit: false,
  },
});
