import { spawn } from "node:child_process";
import { mkdir, rm } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

const webRoot = fileURLToPath(new URL("../", import.meta.url));
const repositoryRoot = fileURLToPath(new URL("../../../", import.meta.url));
const mediaDirectory = fileURLToPath(new URL("../../../docs/media/", import.meta.url));
const temporaryDirectory = fileURLToPath(new URL("../../../output/readme-demo-video/", import.meta.url));
const viteEntrypoint = fileURLToPath(new URL("../node_modules/vite/bin/vite.js", import.meta.url));
const baseUrl = "http://127.0.0.1:4175";
const rawVideoPath = join(temporaryDirectory, "railwait-demo.webm");

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function waitForServer() {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(baseUrl);
      if (response.ok) return;
    } catch {
      // Vite가 아직 기동 중이면 짧게 기다린 뒤 다시 확인합니다.
    }
    await wait(250);
  }
  throw new Error("데모 Vite 서버가 60초 안에 준비되지 않았습니다.");
}

async function capture() {
  await mkdir(mediaDirectory, { recursive: true });
  await rm(temporaryDirectory, { recursive: true, force: true });
  await mkdir(temporaryDirectory, { recursive: true });

  const server = spawn(
    process.execPath,
    [viteEntrypoint, "--host", "127.0.0.1", "--port", "4175", "--strictPort"],
    {
      cwd: webRoot,
      env: { ...process.env, VITE_DEMO_MODE: "true" },
      stdio: "ignore",
      windowsHide: true,
    },
  );

  let browser;
  try {
    await waitForServer();
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({
      locale: "ko-KR",
      timezoneId: "Asia/Seoul",
      viewport: { width: 1280, height: 800 },
      recordVideo: { dir: temporaryDirectory, size: { width: 1280, height: 800 } },
      reducedMotion: "no-preference",
    });
    const browserErrors = [];
    const externalRequests = [];
    await context.route("**/*", async (route) => {
      const requestUrl = route.request().url();
      if (requestUrl.startsWith(baseUrl) || requestUrl.startsWith("data:")) {
        await route.continue();
        return;
      }
      externalRequests.push(requestUrl);
      await route.abort("blockedbyclient");
    });
    context.on("page", (openedPage) => {
      openedPage.on("console", (message) => {
        if (message.type() === "error") browserErrors.push(message.text());
      });
      openedPage.on("pageerror", (error) => browserErrors.push(error.message));
      openedPage.on("request", (request) => {
        const requestUrl = request.url();
        if (!requestUrl.startsWith(baseUrl) && !requestUrl.startsWith("data:")) {
          externalRequests.push(requestUrl);
        }
      });
    });
    const page = await context.newPage();
    const video = page.video();

    await page.clock.install({ time: new Date("2026-07-30T05:32:00.000Z") });
    await page.goto(baseUrl, { waitUntil: "networkidle" });
    await page.waitForTimeout(2_200);

    await page.getByRole("button", { name: "새 대기", exact: true }).click();
    await page.waitForTimeout(1_600);

    await page.getByRole("checkbox", { name: /SRT 시간표/ }).click();
    await page.waitForTimeout(900);
    await page.getByRole("button", { name: "다음" }).click();
    await page.waitForTimeout(1_400);

    await page.getByRole("button", { name: /알림만 받기/ }).click();
    await page.waitForTimeout(900);
    await page.getByRole("button", { name: "다음" }).click();

    const summary = page.getByLabel("시간표 조회 결과 요약");
    await summary.waitFor({ state: "visible" });
    await summary.scrollIntoViewIfNeeded();
    await page.waitForTimeout(1_800);

    const standardSeat = page.getByRole("region", { name: "KTX 033 일반실" });
    await standardSeat.scrollIntoViewIfNeeded();
    await page.waitForTimeout(1_500);

    await standardSeat.getByRole("button", { name: /공식 예매 전 안내 열기/ }).click();
    await page.getByRole("dialog").waitFor({ state: "visible" });
    await page.waitForTimeout(2_000);
    await page.getByRole("button", { name: "공식 좌석 확인 안내 닫기" }).click();
    await page.waitForTimeout(900);

    await standardSeat.getByRole("button", { name: "일반실로 대기" }).click();
    await page.getByRole("button", { name: /일반실 대기 취소/ }).waitFor({ state: "visible" });
    await page.waitForTimeout(1_600);

    await page.getByRole("button", { name: "홈", exact: true }).click();
    await page.getByRole("heading", { name: "활동 중인 대기" }).waitFor({ state: "visible" });
    await page.waitForTimeout(2_400);

    if (browserErrors.length > 0) {
      throw new Error(`브라우저 오류가 발생했습니다: ${browserErrors.join(" | ")}`);
    }
    if (externalRequests.length > 0) {
      throw new Error(`데모 중 외부 요청이 발생했습니다: ${externalRequests.join(" | ")}`);
    }

    await context.close();
    if (!video) throw new Error("Playwright video recorder가 생성되지 않았습니다.");
    await video.saveAs(rawVideoPath);
  } finally {
    await browser?.close();
    server.kill();
  }

  console.log(`원본 녹화: ${rawVideoPath}`);
  console.log(`ffmpeg export 대상: ${mediaDirectory}`);
  console.log(`저장소 루트: ${repositoryRoot}`);
}

await capture();
