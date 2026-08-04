import { defineConfig, devices } from "@playwright/test";
import { fileURLToPath } from "node:url";

import { resolveLiveSmokeStorageStatePath } from "./e2e/support/liveSmokeAuthentication";

const externalBaseUrl = process.env.E2E_BASE_URL?.trim();
const repositoryRoot = fileURLToPath(new URL("../../", import.meta.url));
const storageState = resolveLiveSmokeStorageStatePath(process.env, { repositoryRoot });

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./output/playwright",
  fullyParallel: false,
  // Four simultaneous video/trace-enabled Chromium processes repeatedly crashed the
  // mobile project during navigation on the target personal-server workstation.
  workers: 2,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["line"], ["html", { outputFolder: "output/playwright-report", open: "never" }]] : "line",
  use: {
    baseURL: externalBaseUrl || "http://127.0.0.1:4174",
    browserName: "chromium",
    locale: "ko-KR",
    timezoneId: "Asia/Seoul",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    ...(storageState ? { storageState } : {}),
  },
  projects: [
    {
      name: "desktop-chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "mobile-chromium",
      use: { ...devices["Pixel 7"] },
    },
  ],
  webServer: externalBaseUrl
    ? undefined
    : {
        command: "npm run dev -- --host 127.0.0.1 --port 4174",
        url: "http://127.0.0.1:4174",
        reuseExistingServer: !process.env.CI,
        env: { VITE_DEMO_MODE: "false" },
        timeout: 120_000,
      },
});
