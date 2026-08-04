import {
  expect,
  test,
  type APIResponse,
  type Locator,
  type Page,
  type Request,
} from "@playwright/test";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  expectedLiveKorailActions,
  failureCauseFromLiveTimetable,
  parseFreshLiveKorailTrain,
  parseLiveKorailCapability,
  parseLiveKorailPreflight,
  sanitizedLiveKorailFailureCause,
  type LiveKorailFailureCause,
  type LiveKorailSeatObservation,
} from "../src/features/new-wait/liveKorailSmokeContract";
import {
  liveSmokeAuthFailureMessage,
  liveSmokeAuthenticationFailureMessage,
  resolveLiveSmokeAuthentication,
} from "./support/liveSmokeAuthentication";

const liveEnabled = process.env.RUN_LIVE_PROVIDER_SMOKE === "1"
  && process.env.RUN_LIVE_KORAIL_PROVIDER_SMOKE === "1";
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

const expectedJourney = {
  origin: "서울",
  destination: "부산",
  originNodeId: "NAT010000",
  destinationNodeId: "NAT014445",
  passengerCount: "1",
  departureFrom: "12:00",
  departureTo: "18:00",
} as const;

test.use({ trace: "off", screenshot: "off", video: "off" });

async function selectStation(page: Page, label: "출발역" | "도착역", name: string): Promise<void> {
  const input = page.getByRole("combobox", { name: label });
  await expect(input).toBeEnabled();
  await input.fill(name);
  await page.getByRole("option", { name: new RegExp(`^${name}`) }).click();
}

function isKorailTimetableRequest(request: Request): boolean {
  const url = new URL(request.url());
  return request.method() === "GET"
    && url.pathname.endsWith("/api/v1/timetables")
    && url.searchParams.get("provider") === "korail";
}

function isSrtTimetableRequest(request: Request): boolean {
  const url = new URL(request.url());
  return request.method() === "GET"
    && url.pathname.endsWith("/api/v1/timetables")
    && url.searchParams.get("provider") === "srt";
}

function tomorrowInKst(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(Date.now() + 24 * 60 * 60 * 1_000));
}

function assertExactTimetableRequest(request: Request, departureDate: string): void {
  const url = new URL(request.url());
  expect(url.searchParams.get("provider")).toBe("korail");
  expect(url.searchParams.get("origin")).toBe(expectedJourney.origin);
  expect(url.searchParams.get("destination")).toBe(expectedJourney.destination);
  expect(url.searchParams.get("origin_node_id")).toBe(expectedJourney.originNodeId);
  expect(url.searchParams.get("destination_node_id")).toBe(expectedJourney.destinationNodeId);
  expect(url.searchParams.get("passenger_count")).toBe(expectedJourney.passengerCount);
  expect(url.searchParams.get("departure_from")).toBe(
    `${departureDate}T${expectedJourney.departureFrom}:00+09:00`,
  );
  expect(url.searchParams.get("departure_to")).toBe(
    `${departureDate}T${expectedJourney.departureTo}:00+09:00`,
  );
}

function artifactStation(value: string): string {
  const normalized = " ".concat(value).trim();
  return /^[0-9A-Za-z가-힣 ]{1,40}$/.test(normalized) ? normalized : "configured";
}

function artifactTrainNumber(value: string): string {
  const normalized = value.trim().toUpperCase();
  const canonical = /^\d+$/.test(normalized)
    ? normalized.replace(/^0+/, "") || "0"
    : normalized;
  return /^[0-9A-Z-]{1,40}$/.test(canonical) ? canonical : "unknown";
}

async function attachSanitizedCause(
  route: string,
  requestCount: number,
  cause: LiveKorailFailureCause,
  retryAfterSeconds: number | null = null,
): Promise<void> {
  const body = [
    "provider=KORAIL",
    `route=${route}`,
    "date=default_next_valid_date",
    "time_window=12:00-18:00",
    `request_count=${requestCount}`,
    `cause=${sanitizedLiveKorailFailureCause(cause)}`,
    ...(retryAfterSeconds === null ? [] : [`retry_after_seconds=${retryAfterSeconds}`]),
  ].join("\n");
  await test.info().attach("live-korail-seat-smoke-diagnostic.txt", {
    body: Buffer.from(body, "utf-8"),
    contentType: "text/plain",
  });
}

async function responseJson(response: APIResponse): Promise<unknown> {
  if (!response.ok()) throw new Error("live_preflight_http_error");
  const payload: unknown = await response.json();
  return payload;
}

function escapeRegularExpression(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function assertSeatActions(
  panel: Locator,
  seat: LiveKorailSeatObservation,
  seatMonitoring: boolean,
): Promise<void> {
  const actions = expectedLiveKorailActions(seat.status, seatMonitoring);
  const officialBooking = panel.getByRole("button", { name: /공식 예매 전 안내 열기$/ });
  const officialWaitlist = panel.getByRole("button", {
    name: /공식 예약대기 전 안내 열기$/,
  });
  const cancellationWatch = panel.getByRole("button", { name: /취소표 대기$/ });
  const waitlistWatch = panel.getByRole("button", {
    name: /^(?:일반실|특실) 예약대기$/,
  });

  if (actions.includes("official_booking")) await expect(officialBooking).toBeVisible();
  else await expect(officialBooking).toHaveCount(0);

  if (actions.includes("official_waitlist")) await expect(officialWaitlist).toBeVisible();
  else await expect(officialWaitlist).toHaveCount(0);

  if (actions.includes("add_to_watch")) {
    if (seat.status === "sold_out") await expect(cancellationWatch).toBeVisible();
    else await expect(waitlistWatch).toBeVisible();
  } else {
    await expect(cancellationWatch).toHaveCount(0);
    await expect(waitlistWatch).toHaveCount(0);
  }
}

const seatStatusLabels: Record<LiveKorailSeatObservation["status"], string> = {
  available: "예매 가능",
  limited: "매진 임박",
  standing_plus_seat: "입석+좌석",
  sold_out: "매진",
  waitlist_available: "예약대기 가능",
  not_offered: "예매 불가",
};

test.describe("선택 실행형 KORAIL 운영 좌석 조회 스모크", () => {
  test.describe.configure({ mode: "serial", retries: 0 });
  test.skip(
    !prerequisitesReady,
    "운영 스모크에는 실행 플래그, E2E_BASE_URL과 storage-state 또는 관리자 자격증명이 필요합니다.",
  );

  test("KORAIL 단발 시간표 조회가 일반실·특실의 신선한 공식 상태와 capability별 CTA를 반환한다", async ({ page }) => {
    const origin = expectedJourney.origin;
    const destination = expectedJourney.destination;
    const departureDate = tomorrowInKst();
    const route = `${artifactStation(origin)}→${artifactStation(destination)}`;
    const korailRequests: Request[] = [];
    const srtRequests: Request[] = [];
    let browserErrorCount = 0;
    page.on("request", (request) => {
      if (isKorailTimetableRequest(request)) korailRequests.push(request);
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

    let preflight;
    try {
      preflight = parseLiveKorailPreflight(await responseJson(
        await page.context().request.get("/api/v1/seat-status/status"),
      ));
    } catch {
      await attachSanitizedCause(route, korailRequests.length, "invalid_status_response");
      throw new Error("KORAIL 좌석 조회 제공원 상태 응답 계약을 확인할 수 없습니다.");
    }
    if (preflight.state === "cooldown") {
      expect(korailRequests, "cooldown preflight 뒤에는 시간표 요청을 보내지 않아야 합니다.")
        .toHaveLength(0);
      await attachSanitizedCause(
        route,
        korailRequests.length,
        preflight.cause,
        preflight.retryAfterSeconds,
      );
      throw new Error(
        "KORAIL positive 좌석 snapshot을 확인하지 못했습니다. sanitized artifact를 확인하세요.",
      );
    }

    let capability;
    try {
      capability = parseLiveKorailCapability(await responseJson(
        await page.context().request.get("/api/v1/providers"),
      ));
    } catch {
      await attachSanitizedCause(route, korailRequests.length, "invalid_capability_response");
      throw new Error("KORAIL 실행 capability 응답 계약을 확인할 수 없습니다.");
    }
    if (!capability.seatMonitoring) {
      await attachSanitizedCause(route, korailRequests.length, "invalid_capability_response");
      throw new Error("KORAIL live smoke에는 seat_monitoring=true가 필요합니다.");
    }

    const snapshotMinimumEpochMs = Date.now();
    await expect(page.getByRole("heading", { name: "활동 중인 대기" })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page).toHaveURL(/\/$/);
    await page.getByRole("button", { name: "새 대기" }).first().click();

    const korailProvider = page.getByRole("checkbox", { name: /KTX · KORAIL/ });
    const srtProvider = page.getByRole("checkbox", { name: /^SRT/ });
    await expect(korailProvider).toBeChecked();
    await expect(srtProvider).not.toBeChecked();
    await selectStation(page, "출발역", origin);
    await selectStation(page, "도착역", destination);
    await page.getByRole("button", { name: /^가는 날:/ }).click();
    await page.getByRole("button", { name: "내일", exact: true }).click();
    await page.getByRole("slider", { name: "출발 시작 시간" }).fill("24");
    await page.getByRole("slider", { name: "출발 종료 시간" }).fill("36");
    await page.getByRole("button", { name: /다음/ }).click();

    await expect(page.getByLabel("인원")).toHaveValue("1");

    const timetableResponsePromise = page.waitForResponse((response) => (
      isKorailTimetableRequest(response.request())
    ));
    await page.getByRole("button", { name: /다음/ }).click();

    let timetablePayload: unknown;
    try {
      const timetableResponse = await timetableResponsePromise;
      if (timetableResponse.status() !== 200) throw new Error("timetable_http_error");
      timetablePayload = await timetableResponse.json();
    } catch {
      await attachSanitizedCause(route, korailRequests.length, "invalid_timetable_response");
      throw new Error("KORAIL 시간표 응답 계약을 확인할 수 없습니다.");
    }

    let observedTrain;
    try {
      observedTrain = parseFreshLiveKorailTrain(timetablePayload, snapshotMinimumEpochMs, {
        origin,
        destination,
        departureDate,
        departureFrom: expectedJourney.departureFrom,
        departureTo: expectedJourney.departureTo,
      }, capability.seatMonitoring);
    } catch (error: unknown) {
      await attachSanitizedCause(
        route,
        korailRequests.length,
        failureCauseFromLiveTimetable(timetablePayload),
      );
      throw error;
    }

    const trainCard = page.locator("article.train-result-card").filter({
      hasText: observedTrain.trainNumber,
    });
    const trainNumberPattern = escapeRegularExpression(observedTrain.trainNumber);
    try {
      await expect(trainCard).toHaveCount(1, { timeout: 45_000 });
      const standardPanel = trainCard.getByRole("region", {
        name: new RegExp(`${trainNumberPattern} 일반실$`),
      });
      const firstPanel = trainCard.getByRole("region", {
        name: new RegExp(`${trainNumberPattern} 특실$`),
      });
      await expect(standardPanel.locator(".seat-status-chip")).toHaveText(
        seatStatusLabels[observedTrain.standard.status],
      );
      await expect(firstPanel.locator(".seat-status-chip")).toHaveText(
        seatStatusLabels[observedTrain.first.status],
      );
      await assertSeatActions(standardPanel, observedTrain.standard, capability.seatMonitoring);
      await assertSeatActions(firstPanel, observedTrain.first, capability.seatMonitoring);
    } catch (error: unknown) {
      await attachSanitizedCause(route, korailRequests.length, "invalid_timetable_response");
      throw error;
    }

    expect(korailRequests, "KORAIL 운영 시간표 요청은 한 여정에서 정확히 한 번만 실행해야 합니다.")
      .toHaveLength(1);
    const singleKorailRequest = korailRequests[0];
    if (singleKorailRequest === undefined) throw new Error("KORAIL 시간표 요청이 없습니다.");
    assertExactTimetableRequest(singleKorailRequest, departureDate);
    expect(srtRequests, "KORAIL 단독 조회에서는 SRT 시간표 요청을 보내지 않아야 합니다.")
      .toHaveLength(0);
    expect(capability).toMatchObject({
      enabled: true,
      timetable: true,
      officialBookingLink: true,
      officialWaitlistLink: false,
      reservationOnce: false,
      seatMonitoring: true,
    });
    expect(
      [observedTrain.standard.status, observedTrain.first.status].some(
        (status) => status !== "not_offered",
      ),
      "일반실·특실 중 하나 이상은 실제 행동 가능한 관측 상태여야 합니다.",
    ).toBe(true);
    expect(browserErrorCount, "KORAIL live smoke 중 브라우저 오류가 발생했습니다.").toBe(0);

    await test.info().attach("live-korail-seat-smoke-result.txt", {
      body: Buffer.from(
        [
          "provider=KORAIL",
          `route=${route}`,
          "time_window=12:00-18:00",
          `departure_date=${departureDate}`,
          "korail_request_count=1",
          "srt_request_count=0",
          "source=korail-official-page-browser",
          "seat_monitoring=true",
          `auth_mode=${liveAuthentication.mode}`,
          `train_number=${artifactTrainNumber(observedTrain.trainNumber)}`,
          `departure_at=${observedTrain.departureAt}`,
          `standard_status=${observedTrain.standard.status}`,
          `standard_observed_at=${observedTrain.standard.observedAt}`,
          `standard_evidence_present=${observedTrain.standard.registrationEvidencePresent}`,
          `first_status=${observedTrain.first.status}`,
          `first_observed_at=${observedTrain.first.observedAt}`,
          `first_evidence_present=${observedTrain.first.registrationEvidencePresent}`,
          "browser_error_count=0",
        ].join("\n"),
        "utf-8",
      ),
      contentType: "text/plain",
    });
  });
});
