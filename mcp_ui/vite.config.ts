import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";

// Çıktı tek bir HTML dosyasıdır (JS + CSS gömülü): MCP sunucusu bunu ui://busproje/viewer.html olarak sunar.
// iframe sandbox'ı dışarıdan dosya çekemediği için her şey bu dosyanın içinde olmalı.
export default defineConfig({
  plugins: [react(), viteSingleFile()],
  build: {
    outDir: "../backend/app/mcp_ui",
    emptyOutDir: true,
    chunkSizeWarningLimit: 4000,
  },
});
