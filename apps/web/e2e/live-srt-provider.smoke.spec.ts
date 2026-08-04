import { expect, test, type Locator, type Page, type Request } from "@playwright/test";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  liveSmokeAuthFailureMessage,
  liveSmokeAuthenticationFailureMessage,
  resolveLiveSmokeAuthentication,
} from "./support/liveSmokeAuthentication";

const liveEnabled = process.env.RUN_LIVE_PROVIDER_SMOKE === "1";
const hasExternalService = Boolean(process.env.E2E_BASE_URL?.trim());
const repositoryRoot = resolve(fileURLToPath(new URL("../../../", import.meta.url)));
const liveAuthentication = resolveLiveSmokeAuthentication(process.env, { repositoryRoot });
const prerequisitesReady = liveEnabled && hasExternalService && liveAuthentication.ready;
if (
  liveEnabled
  && hasExternalService
  && !liveAuthentication.ready
  && !liveAuthentication.reasons.includes("authentication_not_configured")
) throw new Error(liveSmokeAuthFailureMessage(liveAuthentication));

const actionableSeatStatuses = [
  "예매 가능",
  "매진 임박",
  "입석+좌석",
  "매진",
  "예약대기 가능",
] as const;

type ActionableSeatStatus = (typeof actionableSeatStatuses)[number];

test.use({ trace: "off", screenshot: "off", video: "off" });

async function selectStation(page: Page, label: "출발역" | "도착역", name: string): Promise<void> {
  const input = page.getByRole("combobox", { name: label });
  await expect(input).toBeEnabled();
  await input.fill(name);
  await page.getByRole("option", { name: new RegExp(`^${name}(?:\\s|$)`) }).click();
}

function isSrtTimetableRequest(request: Request): boolean {
  const url = new URL(request.url());
  return request.method() === "GET"
    && url.pathname.endsWith("/api/v1/timetables")
    && url.searchParams.get("provider") === "srt";
}

async function visibleProtectionCause(page: Page): Promise<string> {
  const knownCauses = [
    { text: "조회 제한", reason: "provider_access_restricted" },
    { text: "시간 초과", reason: "provider_timeout" },
    { text: "연동 안 됨", reason: "source_unavailable" },
    { text: "구간 미지원", reason: "unsupported_route" },
    { text: "확인 필요", reason: "not_observed" },
  ] as const;

  for (const cause of knownCauses) {
    if (await page.getByText(cause.text, { exact: true }).count() > 0) return cause.reason;
  }
  return "no_actionable_official_provider_observation";
}

async function attachSanitizedFailureCause(page: Page, srtRequestCount: number): Promise<void> {
  const body = [
    "provider=SRT",
    "route=수서→부산",
    "date=tomorrow_kst",
    "time_window=12:00-14:00",
    `request_count=${srtRequestCount}`,
    `cause=${await visibleProtectionCause(page)}`,
  ].join("\n");
  await test.info().attach("live-srt-seat-smoke-failure.txt", {
    body: Buffer.from(body, "utf-8"),
    contentType: "text/plain",
  });
}

async function assertStatusAction(panel: Locator, status: ActionableSeatStatus): Promise<void> {
  if (["예매 가능", "매진 임박", "입석+좌석"].includes(status)) {
    await expect(panel.getByRole("button", { name: /공식 예매 전 안내 열기$/ })).toBeVisible();
    return;
  }
  if (status === "매진") {
    await expect(panel.getByRole("button", { name: /취소표 대기$/ })).toBeVisible();
    return;
  }
  await expect(panel.getByRole("button", { name: /공식 예약대기 전 안내 열기$/ })).toBeVisible();
  await expect(panel.getByRole("button", { name: /예약대기$/ })).toBeVisible();
}

test.describe("선택 실행형 SRT 운영 좌석 조회 스모크", () => {
  test.describe.configure({ mode: "serial", retries: 0 });

  test.skip(
    !prerequisitesReady,
    "운영 스모크에는 실행 플래그, E2E_BASE_URL과 storage-state 또는 관리자 자격증명이 필요합니다.",
  );

  test("SRT 수서→부산 단발 조회가 근거 있는 좌석 상태와 상태별 CTA를 반환한다", async ({ page }) => {
    const srtRequests: Request[] = [];
    let browserErrorCount = 0;
    page.on("request", (request) => {
      if (isSrtTimetableRequest(request)) srtRequests.push(request);
    });
    page.on("console", (message) => {
      if (message.type() === "error") browserErrorCount += 1;
    });
    page.on("pageerror", () => {
      browserErrorCount += 1;
    });

    if (!liveAuthentication.ready) throw new Error("운영 스모크 인증 구성이 없습니다.");
    try {
      await liveAuthentication.authenticate(page);
    } catch (error: unknown) {
      throw new Error(liveSmokeAuthenticationFailureMessage(error));
    }
    await expect(page.getByRole("heading", { name: "활동 중인 대기" })).toBeVisible({ timeout: 15_000 });
    await page.getByRole("button", { name: "새 대기" }).first().click();

    const srtProvider = page.getByRole("checkbox", { name: /^SRT/ });
    const korailProvider = page.getByRole("checkbox", { name: /^KTX/ });
    await srtProvider.click();
    await korailProvider.click();
    await expect(srtProvider).toBeChecked();
    await expect(korailProvider).not.toBeChecked();

    await selectStation(page, "출발역", "수서");
    await selectStation(page, "도착역", "부산");
    await page.getByRole("button", { name: /^가는 날:/ }).click();
    await page.getByRole("button", { name: "내일", exact: true }).click();
    await page.getByRole("slider", { name: "출발 종료 시간" }).fill("28");
    await expect(page.getByText("12:00", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("14:00", { exact: true }).first()).toBeVisible();

    await page.getByRole("button", { name: /다음/ }).click();
    await page.getByRole("button", { name: /다음/ }).click();

    const resultSummary = page.getByLabel("시간표 조회 결과 요약");
    try {
      await expect(resultSummary).toContainText("12:00–14:00", { timeout: 45_000 });
    } catch (error: unknown) {
      await attachSanitizedFailureCause(page, srtRequests.length);
      throw error;
    }
    await expect(resultSummary).toContainText("KORAIL 0");
    await expect(resultSummary).toContainText(/SRT [1-9]\d*/);

    const observedPanels = page.locator(".seat-class-panel").filter({
      has: page.locator(".seat-source", { hasText: /^공식 좌석 관측 ·/ }),
    });
    const observedCount = await observedPanels.count();
    if (observedCount === 0) await attachSanitizedFailureCause(page, srtRequests.length);
    expect(
      observedCount,
      "SRT official_provider 좌석 상태를 관측하지 못했습니다. 원인만 담은 sanitized artifact를 확인하세요.",
    ).toBeGreaterThan(0);

    let actionVerified = false;
    for (let index = 0; index < observedCount; index += 1) {
      const panel = observedPanels.nth(index);
      const statusText = (await panel.locator(".seat-status-chip").textContent())?.trim();
      const status = actionableSeatStatuses.find((candidate) => candidate === statusText);
      if (!status) continue;
      await assertStatusAction(panel, status);
      actionVerified = true;
      break;
    }
    if (!actionVerified) await attachSanitizedFailureCause(page, srtRequests.length);
    expect(
      actionVerified,
      "관측한 SRT 좌석 상태에 대응하는 공식 예매·취소표 대기·예약대기 CTA가 없습니다.",
    ).toBe(true);
    expect(srtRequests, "SRT 운영 시간표 요청은 한 여정에서 정확히 한 번만 실행해야 합니다.").toHaveLength(1);
    expect(browserErrorCount, "SRT live smoke 중 브라우저 오류가 발생했습니다.").toBe(0);

    await test.info().attach("live-srt-seat-smoke-result.txt", {
      body: Buffer.from(
        `provider=SRT\nroute=수서→부산\ntime_window=12:00-14:00\nrequest_count=1\nobserved_seat_count=${observedCount}\nbrowser_error_count=0\nauth_mode=${liveAuthentication.mode}`,
        "utf-8",
      ),
      contentType: "text/plain",
    });
  });
});
