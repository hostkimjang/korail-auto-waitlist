import { expect, test, type Page, type Route } from "@playwright/test";

import { selectStation } from "./support/stationSelection";

type Provider = "korail" | "srt";
type SeatClass = "standard" | "first";
type SeatStatus = "available" | "sold_out" | "waitlist_available" | "unknown";
type KorailRefreshResult = "observed" | "provider_access_restricted";

type CreatedWatch = {
  id: string;
  provider: Provider;
  origin: string;
  destination: string;
  travel_date: string;
  time_from: string;
  time_to: string;
  train_numbers: string[];
  seat_class: SeatClass;
  candidates: Array<Record<string, unknown>>;
  status: "draft" | "watching";
  updated_at: string;
};

const evidenceById = new Map<string, SeatStatus>([
  ["10000000-0000-4000-8000-000000000001", "available"],
  ["10000000-0000-4000-8000-000000000002", "sold_out"],
  ["20000000-0000-4000-8000-000000000301", "sold_out"],
  ["20000000-0000-4000-8000-000000000302", "waitlist_available"],
  ["30000000-0000-4000-8000-000000000301", "sold_out"],
  ["30000000-0000-4000-8000-000000000302", "waitlist_available"],
]);

type LocalApiState = {
  createRequests: number;
  createdWatches: number;
  refreshRequests: number;
  korailRefreshResult: KorailRefreshResult;
  startRequests: number;
  expiredConflictReturned: boolean;
  startResponseLostReturned: boolean;
};

const localApiStates = new WeakMap<Page, LocalApiState>();

function localApiState(page: Page): LocalApiState {
  const state = localApiStates.get(page);
  if (!state) throw new Error("local E2E API state was not installed");
  return state;
}

function timestamp(offsetMilliseconds = 0): string {
  return new Date(Date.now() + offsetMilliseconds).toISOString();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function json(route: Route, body: unknown, status = 200): Promise<void> {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

function stationCatalog(provider: Provider): Record<string, unknown> {
  return {
    provider,
    source: "TAGO",
    retrieved_at: "2026-07-30T00:00:00Z",
    catalog_scope: "intercity_station_guide_intersection",
    provider_membership: "not_verified_by_source",
    note: "E2E 고정 역 카탈로그",
    stations: [
      { node_id: "N-SEOUL", name: "서울", city_code: "11", city_name: "서울" },
      { node_id: "N-BUSAN", name: "부산", city_code: "26", city_name: "부산" },
    ],
  };
}

function seat(
  seatClass: SeatClass,
  status: SeatStatus,
  evidenceId: string,
  officialUrl: string,
  unobservedReason = "provider_access_restricted",
): Record<string, unknown> {
  if (status === "unknown") {
    return {
      seat_class: seatClass,
      status,
      provenance: { kind: "not_observed", reason: unobservedReason },
      registration_evidence_id: null,
      actions: [{ kind: "official_check", url: officialUrl }],
    };
  }
  const officialKind = status === "waitlist_available" ? "official_waitlist" : "official_check";
  return {
    seat_class: seatClass,
    status,
    provenance: {
      kind: "official_provider",
      source: "e2e-local-provider",
      observed_at: timestamp(-5_000),
    },
    registration_evidence_id: evidenceId,
    actions: [
      { kind: officialKind, url: officialUrl },
      ...(["sold_out", "waitlist_available"].includes(status) ? [{ kind: "add_to_watch" }] : []),
    ],
  };
}

function timetable(
  provider: Provider,
  searchParams: URLSearchParams,
  refreshed = false,
  korailRefreshResult: KorailRefreshResult = "observed",
): Array<Record<string, unknown>> {
  const departureDate = searchParams.get("departure_from")?.slice(0, 10) ?? "2026-07-31";
  const start = searchParams.get("departure_from")?.slice(11, 16) ?? "12:00";
  const startHour = Number(start.slice(0, 2));
  const officialUrl = provider === "korail"
    ? "https://www.korail.com/ticket/search"
    : "https://etk.srail.kr/hpg/hra/01/selectScheduleList.do?pageId=TK0101010000";
  const at = (hourOffset: number, minute: number): string => (
    `${departureDate}T${String(startHour + hourOffset).padStart(2, "0")}:${String(minute).padStart(2, "0")}:00+09:00`
  );

  if (provider === "korail") {
    const hasRestrictedAccess = refreshed && korailRefreshResult === "provider_access_restricted";
    return [
      {
        provider,
        train_number: "KTX 001",
        train_type: "KTX",
        origin: "서울",
        destination: "부산",
        departure_at: at(0, 20),
        arrival_at: at(3, 0),
        adult_fare: 59_800,
        timetable_source: "official",
        timetable_retrieved_at: timestamp(-5_000),
        official_booking_url: officialUrl,
        seat_classes: refreshed && !hasRestrictedAccess
          ? [
              seat("standard", "available", "10000000-0000-4000-8000-000000000001", officialUrl),
              seat("first", "sold_out", "10000000-0000-4000-8000-000000000002", officialUrl),
            ]
          : [
              seat(
                "standard",
                "unknown",
                "",
                officialUrl,
                hasRestrictedAccess ? "provider_access_restricted" : "source_not_configured",
              ),
              seat(
                "first",
                "unknown",
                "",
                officialUrl,
                hasRestrictedAccess ? "provider_access_restricted" : "source_not_configured",
              ),
            ],
      },
      {
        provider,
        train_number: "KTX 003",
        train_type: "KTX",
        origin: "서울",
        destination: "부산",
        departure_at: at(1, 0),
        arrival_at: at(3, 40),
        adult_fare: 59_800,
        timetable_source: "official",
        timetable_retrieved_at: timestamp(-5_000),
        official_booking_url: officialUrl,
        seat_classes: [
          seat(
            "standard",
            "unknown",
            "",
            officialUrl,
            hasRestrictedAccess ? "provider_access_restricted" : "source_not_configured",
          ),
          seat(
            "first",
            "unknown",
            "",
            officialUrl,
            hasRestrictedAccess ? "provider_access_restricted" : "source_not_configured",
          ),
        ],
      },
    ];
  }

  return [{
    provider,
    train_number: "SRT 301",
    train_type: "SRT",
    origin: "서울",
    destination: "부산",
    departure_at: at(0, 40),
    arrival_at: at(3, 10),
    adult_fare: 52_600,
    timetable_source: "official",
    timetable_retrieved_at: timestamp(-5_000),
    official_booking_url: officialUrl,
    seat_classes: [
      seat(
        "standard",
        "sold_out",
        refreshed
          ? "30000000-0000-4000-8000-000000000301"
          : "20000000-0000-4000-8000-000000000301",
        officialUrl,
      ),
      seat(
        "first",
        "waitlist_available",
        refreshed
          ? "30000000-0000-4000-8000-000000000302"
          : "20000000-0000-4000-8000-000000000302",
        officialUrl,
      ),
    ],
  }];
}

function buildWatch(payload: Record<string, unknown>, id: string): CreatedWatch {
  const candidates = Array.isArray(payload.candidates) ? payload.candidates : [];
  const candidate = isRecord(candidates[0]) ? candidates[0] : {};
  const evidenceId = typeof candidate.registration_evidence_id === "string"
    ? candidate.registration_evidence_id
    : "";
  const seatClass = candidate.seat_class === "first" ? "first" : "standard";
  const status = evidenceById.get(evidenceId) ?? "unknown";
  const observedAt = timestamp(-5_000);
  const createdAt = timestamp();
  const registrationEvidence = {
    id: evidenceId,
    status,
    provenance: { kind: "official_provider", source: "e2e-local-provider", observed_at: observedAt },
    created_at: createdAt,
    registration_valid_until: timestamp(5 * 60 * 1_000),
  };
  const trainNumber = typeof candidate.train_number === "string" ? candidate.train_number : "열차 미정";
  return {
    id,
    provider: payload.provider === "srt" ? "srt" : "korail",
    origin: typeof payload.origin === "string" ? payload.origin : "서울",
    destination: typeof payload.destination === "string" ? payload.destination : "부산",
    travel_date: typeof payload.travel_date === "string" ? payload.travel_date : "2026-07-31",
    time_from: typeof payload.time_from === "string" ? payload.time_from : "12:00:00",
    time_to: typeof payload.time_to === "string" ? payload.time_to : "15:00:00",
    train_numbers: [trainNumber],
    seat_class: seatClass,
    candidates: [{
      ...candidate,
      id: `candidate-${id}`,
      priority: 1,
      state: "pending",
      registration_evidence: registrationEvidence,
    }],
    status: "draft",
    updated_at: createdAt,
  };
}

async function installLocalApi(page: Page): Promise<void> {
  const watches: CreatedWatch[] = [];
  const watchByCreateKey = new Map<string, CreatedWatch>();
  let watchSequence = 0;
  const state: LocalApiState = {
    createRequests: 0,
    createdWatches: 0,
    refreshRequests: 0,
    korailRefreshResult: "observed",
    startRequests: 0,
    expiredConflictReturned: false,
    startResponseLostReturned: false,
  };
  localApiStates.set(page, state);

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (path.endsWith("/events")) {
      await route.fulfill({ status: 204, body: "" });
      return;
    }
    if (path.endsWith("/auth/status")) {
      await json(route, { configured: true, authenticated: true, registration_allowed: false });
      return;
    }
    if (path.endsWith("/notifications/channels")) {
      await json(route, []);
      return;
    }
    if (path.endsWith("/stations")) {
      const provider = url.searchParams.get("provider") === "srt" ? "srt" : "korail";
      await json(route, stationCatalog(provider));
      return;
    }
    if (path.endsWith("/timetables")) {
      const provider = url.searchParams.get("provider") === "srt" ? "srt" : "korail";
      await json(route, timetable(provider, url.searchParams));
      return;
    }
    if (path.endsWith("/seat-status/refresh") && request.method() === "POST") {
      state.refreshRequests += 1;
      const payload: unknown = request.postDataJSON();
      const provider: Provider = isRecord(payload) && payload.provider === "srt" ? "srt" : "korail";
      const params = new URLSearchParams({
        departure_from: isRecord(payload) && typeof payload.departure_from === "string"
          ? payload.departure_from
          : "2026-07-31T12:00:00+09:00",
      });
      await json(route, timetable(provider, params, true, state.korailRefreshResult));
      return;
    }
    if (path.endsWith("/watches") && request.method() === "GET") {
      await json(route, watches);
      return;
    }
    if (path.endsWith("/watches") && request.method() === "POST") {
      state.createRequests += 1;
      const payload: unknown = request.postDataJSON();
      if (!isRecord(payload)) {
        await json(route, { detail: "invalid local E2E payload" }, 422);
        return;
      }
      if (!state.expiredConflictReturned) {
        state.expiredConflictReturned = true;
        await json(route, {
          detail: {
            code: "registration_evidence_conflict",
            reason: "expired",
            message: "좌석 등록 근거가 만료되었습니다. 좌석 상태를 다시 조회해 주세요.",
          },
        }, 409);
        return;
      }
      const idempotencyKey = request.headers()["idempotency-key"] ?? "";
      const existing = watchByCreateKey.get(idempotencyKey);
      if (existing) {
        await json(route, existing, 201);
        return;
      }
      const watch = buildWatch(payload, `watch-e2e-${++watchSequence}`);
      watches.unshift(watch);
      watchByCreateKey.set(idempotencyKey, watch);
      state.createdWatches += 1;
      await json(route, watch, 201);
      return;
    }
    const startMatch = path.match(/\/watches\/([^/]+)\/start$/);
    if (startMatch && request.method() === "POST") {
      state.startRequests += 1;
      const watch = watches.find((item) => item.id === startMatch[1]);
      if (!watch) {
        await json(route, { detail: "watch not found" }, 404);
        return;
      }
      watch.status = "watching";
      if (!state.startResponseLostReturned) {
        state.startResponseLostReturned = true;
        await json(route, { detail: "start response lost after commit" }, 503);
        return;
      }
      await json(route, watch);
      return;
    }
    await json(route, { detail: `unhandled local E2E route: ${request.method()} ${path}` }, 404);
  });
}

test.beforeEach(async ({ page }) => {
  await installLocalApi(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "활동 중인 대기" })).toBeVisible();
});

test("여정 선택부터 만료 근거 1회 복구, 복수 좌석 즉시 등록과 홈 전체 반영까지 이어진다", async ({ page }) => {
  await page.getByRole("button", { name: "새 대기" }).first().click();
  await expect(page.getByRole("heading", { name: "어디로 떠나세요?" })).toBeVisible();

  await page.getByRole("checkbox", { name: /^SRT/ }).click();
  await selectStation(page, "출발역", "서울");
  await selectStation(page, "도착역", "부산");
  await page.getByRole("button", { name: /^가는 날:/ }).click();
  await page.getByRole("button", { name: "이번 주말" }).click();

  await page.getByRole("button", { name: /다음/ }).click();
  await expect(page.getByRole("heading", { name: "어떤 좌석을 찾을까요?" })).toBeVisible();
  await page.getByRole("button", { name: /다음/ }).click();
  await expect(page.getByRole("heading", { name: "공식 시간표에서 관심 열차를 고르세요" })).toBeVisible();

  let available = page.getByRole("article", { name: "KTX 001" });
  await expect(available.getByText("서버 좌석 조회 미설정")).toHaveCount(2);
  await expect(available.getByRole("button", { name: /일반실.*대기/ })).toHaveCount(0);

  await expect(page.getByRole("button", { name: /공식 좌석 상태 가져오기|Companion|확장/ }))
    .toHaveCount(0);
  await page.getByRole("button", { name: "서버에서 좌석 상태 다시 조회" }).click();

  available = page.getByRole("article", { name: "KTX 001" });
  await expect(available.getByText("예매 가능")).toBeVisible();
  await expect(available.getByText("매진", { exact: true })).toBeVisible();
  await expect(available.getByRole("button", { name: "일반실 공식 예매 전 안내 열기" })).toBeVisible();
  await expect(available.getByRole("button", { name: "특실 취소표 대기" })).toBeVisible();
  await expect(page.getByRole("button", { name: /공식 좌석 상태 가져오기|Companion|확장/ }))
    .toHaveCount(0);

  const restricted = page.getByRole("article", { name: "KTX 003" });
  await expect(restricted.getByText("서버 좌석 조회 미설정")).toHaveCount(2);
  await expect(restricted.getByText(
    "서버의 KORAIL Chromium adapter 설정을 확인한 뒤 다시 조회해 주세요.",
  )).toHaveCount(2);
  await expect(restricted.getByRole("button", { name: /관심 열차에 추가/ })).toHaveCount(0);

  const selectable = page.getByRole("article", { name: "SRT 301" });
  await expect(selectable.getByText("매진", { exact: true })).toBeVisible();
  await expect(selectable.getByText("예약대기 가능", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "오전 09:00부터 12:00까지" }).click();
  await page.getByRole("button", { name: "적용·재조회" }).click();
  await expect(page.getByLabel("시간표 조회 결과 요약")).toContainText("09:00–12:00");

  await page.getByRole("article", { name: "SRT 301" }).getByRole("button", { name: "일반실 취소표 대기" }).click();
  await expect(page.getByRole("button", { name: "일반실 다시 등록" })).toBeVisible();
  await page.getByRole("button", { name: "일반실 다시 등록" }).click();
  await expect(page.getByRole("button", { name: "일반실 대기 취소" })).toBeVisible();
  expect(localApiState(page)).toMatchObject({
    createRequests: 3,
    createdWatches: 1,
    refreshRequests: 2,
    startRequests: 2,
    expiredConflictReturned: true,
    startResponseLostReturned: true,
  });
  await page.getByRole("article", { name: "SRT 301" }).getByRole("button", { name: "특실 예약대기" }).click();
  await expect(page.getByRole("button", { name: "특실 대기 취소" })).toBeVisible();
  expect(localApiState(page)).toMatchObject({
    createRequests: 4,
    createdWatches: 2,
    refreshRequests: 2,
    startRequests: 3,
  });
  await expect(page.getByRole("button", { name: "등록 완료" })).toHaveCount(0);
  await expect(page.getByLabel("등록한 관심 열차 대기")).toHaveCount(0);

  await page.getByRole("button", { name: "홈", exact: true }).first().click();
  await expect(page.getByRole("heading", { name: "활동 중인 대기" })).toBeVisible();
  await expect(page.getByLabel("전체 2건 모두 표시 중")).toBeVisible();
  await expect(page.locator(".watch-row")).toHaveCount(2);
  await expect(page.locator(".watch-row").filter({ hasText: "일반실 · 매진" })).toHaveCount(1);
  await expect(page.locator(".watch-row").filter({ hasText: "특실 · 예약대기 가능" })).toHaveCount(1);
});

test("KORAIL 공식 조회 제한 결과는 두 좌석을 미확인으로 유지하고 재시도와 행동을 숨긴다", async ({ page }) => {
  localApiState(page).korailRefreshResult = "provider_access_restricted";

  await page.getByRole("button", { name: "새 대기" }).first().click();
  await selectStation(page, "출발역", "서울");
  await selectStation(page, "도착역", "부산");
  await page.getByRole("button", { name: /^가는 날:/ }).click();
  await page.getByRole("button", { name: "이번 주말" }).click();
  await page.getByRole("button", { name: /다음/ }).click();
  await page.getByRole("button", { name: /다음/ }).click();

  await expect(page.getByRole("heading", { name: "공식 시간표에서 관심 열차를 고르세요" }))
    .toBeVisible();
  await page.getByRole("button", { name: "서버에서 좌석 상태 다시 조회" }).click();
  await expect.poll(() => localApiState(page).refreshRequests).toBe(1);

  const restricted = page.getByRole("article", { name: "KTX 001" });
  await expect(restricted.getByText("조회 제한", { exact: true })).toHaveCount(2);
  await expect(restricted.getByText(
    "운영사가 현재 좌석 조회를 제한해 상태를 가져오지 못했습니다.",
  )).toHaveCount(2);
  await expect(restricted.getByRole("button")).toHaveCount(0);
  await expect(page.getByText("공식 좌석 조회가 제한되었습니다", { exact: true })).toBeVisible();
  await expect(page.getByText(
    "보호 대기 시간 동안 서버는 운영사에 다시 요청하지 않습니다. 좌석은 미확인 상태로 유지되며 예매·대기 행동을 제공하지 않습니다.",
  )).toBeVisible();
  await expect(page.getByRole("button", { name: "서버에서 좌석 상태 다시 조회" })).toHaveCount(0);
});
