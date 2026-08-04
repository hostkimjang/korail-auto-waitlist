import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  // Local development shares the repository-level .env with Docker Compose.
  // Vite only exposes VITE_* names to browser code; server secrets stay private.
  envDir: "../..",
  build: {
    outDir: "dist/client",
  },
  optimizeDeps: {
    include: ["react", "react-dom/client"],
  },
  server: {
    host: "0.0.0.0",
    allowedHosts: ["terminal.local"],
    proxy: {
      "/api": {
        target: "http://127.0.0.1",
      },
    },
    warmup: {
      clientFiles: ["./src/main.tsx"],
    },
  },
  plugins: [react()],
});
