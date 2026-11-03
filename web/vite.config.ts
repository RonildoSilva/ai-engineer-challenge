import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Porta 8020: a mesma liberada no CORS da API (`API_CORS_ORIGINS`), em dev e no preview.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 8020, strictPort: true, host: "127.0.0.1" },
  preview: { port: 8020, strictPort: true, host: "127.0.0.1" },
  build: { outDir: "dist", sourcemap: false },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
