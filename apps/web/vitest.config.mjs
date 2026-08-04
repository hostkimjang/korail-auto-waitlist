import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.js"],
    include: ["tests/**/*.test.{js,jsx,ts,tsx}"],
    exclude: ["tests/sites-worker.test.mjs"],
  },
});
