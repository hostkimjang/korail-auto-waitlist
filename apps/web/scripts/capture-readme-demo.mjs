/* global window */

import { spawn } from "node:child_process";
import { mkdir, rm } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

import {
  clickWithDemoMotion,
  focusWithDemoMotion,
  hideDemoCursor,
  installDemoCaptureMotion,
  smoothScrollLocatorIntoView,
  smoothScrollTo,
  transitionWithDemoMotion,
} from "./demo-capture-motion.mjs";

const webRoot = fileURLToPath(new URL("../", import.meta.url));
const repositoryRoot = fileURLToPath(new URL("../../../", import.meta.url));
const mediaDirectory = fileURLToPath(new URL("../../../docs/media/", import.meta.url));
const temporaryDirectory = fileURLToPath(new URL("../../../output/readme-demo-video/", import.meta.url));
const viteEntrypoint = fileURLToPath(new URL("../node_modules/vite/bin/vite.js", import.meta.url));
const baseUrl = "http://127.0.0.1:4175";
const rawVideoPath = join(temporaryDirectory, "railwait-demo.webm");

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function advanceReservationLifecycle(page, stage) {
  return page.evaluate((nextStage) => {
    const bridge = window.__RAILWAIT_DEMO_CAPTURE__;
    if (!bridge) throw new Error("README 예약 진행 데모 드라이버를 찾지 못했습니다.");
    return bridge.advance(nextStage);
  }, stage);
}

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
      env: {
        ...process.env,
        VITE_DEMO_CAPTURE_SCENARIO: "reservation-lifecycle",
        VITE_DEMO_MODE: "true",
      },
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

    await page.clock.setFixedTime(new Date("2026-07-30T05:32:00.000Z"));
    await page.goto(baseUrl, { waitUntil: "networkidle" });
    await installDemoCaptureMotion(page);
    await page.waitForTimeout(800);

    await clickWithDemoMotion(page, page.getByRole("button", { name: "새 대기", exact: true }), {
      pageTransition: true,
      resultHoldMs: 550,
    });

    await clickWithDemoMotion(page, page.getByRole("checkbox", { name: /SRT 시간표/ }), {
      resultHoldMs: 280,
    });
    await clickWithDemoMotion(page, page.getByRole("button", { name: "다음" }), {
      pageTransition: true,
      resultHoldMs: 420,
    });

    const automaticReservation = page.getByRole("button", {
      name: /좌석 재발견마다 자동 예매/,
    });
    await clickWithDemoMotion(page, automaticReservation, {
      resultHoldMs: 420,
    });
    if (await automaticReservation.getAttribute("aria-pressed") !== "true") {
      throw new Error("자동 예매 선택이 화면에 반영되지 않았습니다.");
    }
    await page.getByText(
      "자동 결제는 하지 않습니다. 결제 필요 알림을 받은 뒤 공식 플랫폼에서 직접 결제하세요.",
      { exact: true },
    ).waitFor({ state: "visible" });
    await clickWithDemoMotion(page, page.getByRole("button", { name: "다음" }), {
      pageTransition: true,
      resultHoldMs: 300,
    });

    const summary = page.getByLabel("시간표 조회 결과 요약");
    await summary.waitFor({ state: "visible" });
    await page.waitForTimeout(600);

    const standardSeat = page.getByRole("region", { name: "KTX 033 일반실" });
    await smoothScrollLocatorIntoView(page, standardSeat, {
      align: 0.5,
      force: true,
      hideCursor: true,
      settleMs: 320,
    });

    await clickWithDemoMotion(page, standardSeat.getByRole("button", { name: "일반실로 대기" }), {
      resultHoldMs: 420,
    });
    await page.getByRole("button", { name: /일반실 대기 취소/ }).waitFor({ state: "visible" });
    await standardSeat.getByText(
      "좌석 재발견마다 자동 예매 · 결제 전 중단",
      { exact: true },
    ).waitFor({ state: "visible" });
    await focusWithDemoMotion(page, standardSeat, {
      zoomScale: 1.12,
      holdMs: 620,
      scroll: false,
    });

    await smoothScrollTo(page, 0, { durationMs: 620, hideCursor: true, settleMs: 300 });
    await clickWithDemoMotion(page, page.getByRole("button", { name: "홈", exact: true }), {
      pageTransition: true,
      resultHoldMs: 420,
    });
    await page.getByRole("heading", { name: "활동 중인 대기" }).waitFor({ state: "visible" });

    const activeWatch = page.getByRole("article").filter({ hasText: "KTX 033" }).first();
    await activeWatch.getByText("감시 중", { exact: true }).waitFor({ state: "visible" });
    const policySwitch = activeWatch.getByRole("switch", {
      name: /KTX 033 일반실 좌석 재발견마다 자동 예매 설정/,
    });
    if (await policySwitch.getAttribute("aria-checked") !== "true") {
      throw new Error("등록한 대기의 자동 예매 정책이 홈 화면에 반영되지 않았습니다.");
    }
    await smoothScrollLocatorIntoView(page, activeWatch, {
      align: 0.48,
      force: true,
      hideCursor: true,
      settleMs: 420,
    });

    await transitionWithDemoMotion(page, async () => {
      await advanceReservationLifecycle(page, "seat_found");
      await activeWatch.getByText(
        "좌석 발견 · 감시 계속",
        { exact: true },
      ).waitFor({ state: "visible" });
      await activeWatch.getByText(
        "일반실 · 예매 가능 · 데모 관측 14:33",
        { exact: true },
      ).waitFor({ state: "visible" });
    }, { resultHoldMs: 720 });

    const notificationCenter = page.getByRole("region", { name: "실시간 알림" });
    await transitionWithDemoMotion(page, async () => {
      await advanceReservationLifecycle(page, "reserving");
      await activeWatch.getByText("예매 진행 중", { exact: true }).waitFor({ state: "visible" });
      await activeWatch.getByText(/예매 시도 중/).waitFor({ state: "visible" });
      await notificationCenter.getByText(
        "예매를 진행하고 있습니다",
        { exact: true },
      ).waitFor({ state: "visible" });
      await notificationCenter.getByText("예매 시작", { exact: true }).waitFor({ state: "visible" });
    }, { resultHoldMs: 1_120 });

    await smoothScrollTo(page, 0, { durationMs: 560, hideCursor: true, settleMs: 260 });
    await transitionWithDemoMotion(page, async () => {
      await advanceReservationLifecycle(page, "payment_required");
      await page.getByRole("heading", { name: "결제 대기 1건" }).waitFor({ state: "visible" });
      await page.getByRole("button", { name: /공식 결제 열기/ }).waitFor({ state: "visible" });
      await page.getByText("결제기한 미제공", { exact: true }).waitFor({ state: "visible" });
      await notificationCenter.getByText(
        "결제 직전까지 예매되었습니다",
        { exact: true },
      ).waitFor({ state: "visible" });
    }, { resultHoldMs: 900 });

    const paymentCard = page.getByRole("article").filter({ hasText: "KTX 033" }).first();
    await focusWithDemoMotion(page, paymentCard, {
      zoomScale: 1.05,
      holdMs: 1_100,
      scroll: false,
    });
    await hideDemoCursor(page);
    await page.waitForTimeout(720);

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
