import { expect, test, type Page } from "@playwright/test";


const enabled = process.env.RUN_FULLSTACK_E2E === "1";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function evidenceIdsFromTimetable(payload: unknown, provider: "korail" | "srt"): string[] {
  if (!Array.isArray(payload)) return [];
  const evidenceIds: string[] = [];
  for (const rawTrain of payload) {
    if (!isRecord(rawTrain) || rawTrain.provider !== provider) continue;
    if (!Array.isArray(rawTrain.seat_classes)) continue;
    for (const rawSeat of rawTrain.seat_classes) {
      if (!isRecord(rawSeat)) continue;
      const evidenceId = rawSeat.registration_evidence_id;
      if (typeof evidenceId === "string" && evidenceId.length > 0) {
        evidenceIds.push(evidenceId);
      }
    }
  }
  return evidenceIds;
}

function korailBrowserSeatStatuses(payload: unknown): string[] {
  if (!Array.isArray(payload)) return [];
  const statuses: string[] = [];
  for (const rawTrain of payload) {
    if (
      !isRecord(rawTrain)
      || rawTrain.provider !== "korail"
      || rawTrain.train_number !== "9001"
      || !Array.isArray(rawTrain.seat_classes)
    ) continue;
    for (const rawSeat of rawTrain.seat_classes) {
      if (!isRecord(rawSeat) || !isRecord(rawSeat.provenance)) continue;
      if (
        rawSeat.provenance.kind === "official_provider"
        && rawSeat.provenance.source === "korail-official-page-browser"
        && typeof rawSeat.status === "string"
      ) statuses.push(rawSeat.status);
    }
  }
  return statuses;
}

function evidenceIdFromWatchRequest(payload: unknown): string | null {
  if (!isRecord(payload) || !Array.isArray(payload.candidates)) return null;
  const candidate = payload.candidates[0];
  if (!isRecord(candidate)) return null;
  const evidenceId = candidate.registration_evidence_id;
  return typeof evidenceId === "string" && evidenceId.length > 0 ? evidenceId : null;
}

async function registerEphemeralAdmin(page: Page): Promise<void> {
  await expect(page.getByRole("heading", { name: "초기 관리자 등록" })).toBeVisible();
  await page.getByLabel("관리자 ID").fill("fullstack.e2e");
  await page.getByLabel("비밀번호", { exact: true }).fill("e2e-only-password-2026");
  await page.getByLabel("비밀번호 확인").fill("e2e-only-password-2026");
  await page.getByRole("button", { name: "관리자 계정 만들기" }).click();
  await expect(page.getByRole("heading", { name: "활동 중인 대기" })).toBeVisible();
}

async function configureTelegramChannel(page: Page): Promise<void> {
  await page.getByRole("button", { name: "설정" }).first().click();
  await expect(page.getByRole("heading", { name: "알림 채널" })).toBeVisible();
  const telegram = page.locator(".setting-row").filter({ hasText: "텔레그램" });
  await telegram.getByRole("button", { name: "연결" }).click();
  await page.getByLabel("표시 이름").fill("격리 E2E 알림");
  await page.getByLabel("Bot token").fill("000000000:e2e-synthetic-token");
  await page.getByLabel("Chat ID").fill("100000001");
  await page.getByRole("button", { name: "저장" }).click();
  await expect(
    telegram.getByText("격리 E2E 알림 · 사용 중", { exact: true }),
  ).toBeVisible();
}

async function selectStation(
  page: Page,
  label: "출발역" | "도착역",
  station: string,
): Promise<void> {
  const input = page.getByRole("combobox", { name: label });
  await expect(input).toBeEnabled();
  await input.fill(station);
  await page.getByRole("option", { name: new RegExp(`^${station}`) }).click();
}

test.describe("격리 Compose 전체 스택", () => {
  test.skip(!enabled, "RUN_FULLSTACK_E2E=1인 격리 Compose 실행에서만 수행합니다.");

  test("KORAIL Chromium DOM과 SRT worker 상태 전이가 한 사용자 흐름으로 이어진다", async ({ page }) => {
    test.setTimeout(120_000);
    const korailEvidenceIds = new Set<string>();
    const srtEvidenceIds = new Set<string>();
    let observedKorailStatuses: string[] = [];
    const watchRequestEvidenceIds: string[] = [];
    const startStatuses: string[] = [];

    await page.addInitScript(() => {
      window.open = (url?: string | URL, target?: string, features?: string): Window | null => {
        window.sessionStorage.setItem(
          "fullstack-official-handoff",
          JSON.stringify({ url: String(url ?? ""), target, features }),
        );
        return null;
      };
    });

    page.on("response", async (response) => {
      const url = new URL(response.url());
      if (
        response.status() === 200
        && response.request().method() === "POST"
        && /\/api\/v1\/watches\/[^/]+\/start$/.test(url.pathname)
      ) {
        try {
          const payload: unknown = await response.json();
          if (isRecord(payload) && typeof payload.status === "string") {
            startStatuses.push(payload.status);
          }
        } catch {
          return;
        }
        return;
      }
      if (response.status() !== 200 || url.pathname !== "/api/v1/timetables") return;
      try {
        const payload: unknown = await response.json();
        const korailStatuses = korailBrowserSeatStatuses(payload);
        if (korailStatuses.length > 0) observedKorailStatuses = korailStatuses;
        const provider = url.searchParams.get("provider");
        if (provider !== "korail" && provider !== "srt") return;
        for (const evidenceId of evidenceIdsFromTimetable(payload, provider)) {
          if (provider === "korail") korailEvidenceIds.add(evidenceId);
          if (provider === "srt") srtEvidenceIds.add(evidenceId);
        }
      } catch {
        return;
      }
    });
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (request.method() !== "POST" || url.pathname !== "/api/v1/watches") return;
      try {
        const evidenceId = evidenceIdFromWatchRequest(request.postDataJSON());
        if (evidenceId !== null) watchRequestEvidenceIds.push(evidenceId);
      } catch {
        return;
      }
    });

    await page.goto("/");
    await registerEphemeralAdmin(page);
    await configureTelegramChannel(page);
    await page.getByRole("button", { name: "새 대기" }).first().click();
    await expect(page.getByRole("heading", { name: "어디로 떠나세요?" })).toBeVisible();

    await selectStation(page, "출발역", "서울");
    await selectStation(page, "도착역", "부산");
    await page.getByRole("button", { name: "다음", exact: true }).click();
    await page.getByRole("button", { name: "다음", exact: true }).click();
    await expect(page.getByRole("heading", { name: "공식 시간표에서 관심 열차를 고르세요" }))
      .toBeVisible();

    const train9001 = page.locator("article.train-result-card").filter({ hasText: "9001" });
    await expect(train9001).toHaveCount(1, { timeout: 30_000 });
    await expect(train9001).toContainText("KTX");
    await expect(train9001.getByText("예매 가능", { exact: true })).toBeVisible();
    await expect(train9001.getByText("매진", { exact: true })).toBeVisible();
    const korailBookingHandoff = train9001.getByRole(
      "button",
      { name: /9001 일반실 공식 예매 전 안내 열기/ },
    );
    await expect(korailBookingHandoff).toBeVisible();
    await korailBookingHandoff.click();
    const handoffDialog = page.getByRole(
      "dialog",
      { name: /9001 공식 좌석 확인 전 안내/ },
    );
    await expect(handoffDialog).toBeVisible();
    const journeySummary = handoffDialog.getByLabel("선택 열차 요약");
    await expect(journeySummary).toContainText("서울 → 부산");
    await expect(journeySummary).toContainText("13:00 → 15:30");
    await expect(journeySummary).toContainText(/9001 · 일반실/);
    const officialPageButton = handoffDialog.getByRole(
      "button",
      { name: /공식 페이지 열기.*새 탭/ },
    );
    await expect(officialPageButton).toBeEnabled();
    const pageCountBeforeHandoff = page.context().pages().length;
    await officialPageButton.click();
    await expect.poll(() => page.evaluate(() => (
      window.sessionStorage.getItem("fullstack-official-handoff")
    ))).not.toBeNull();
    const handoff = await page.evaluate((): unknown => {
      const rawHandoff = window.sessionStorage.getItem("fullstack-official-handoff");
      return rawHandoff === null ? null : JSON.parse(rawHandoff);
    });
    expect(handoff).toEqual({
      url: "https://www.korail.com/ticket/search/general",
      target: "_blank",
      features: "noopener,noreferrer",
    });
    expect(page.context().pages()).toHaveLength(pageCountBeforeHandoff);
    await handoffDialog.getByRole("button", { name: "공식 좌석 확인 안내 닫기" }).click();
    await expect(handoffDialog).toBeHidden();
    await expect(korailBookingHandoff).toBeFocused();
    const korailFirstWatch = train9001.getByRole("region", { name: /특실/ })
      .getByRole("button", { name: "특실 취소표 대기" });
    await expect(korailFirstWatch).toBeVisible();
    await expect.poll(() => [...observedKorailStatuses].sort()).toEqual([
      "available",
      "sold_out",
    ]);
    expect(korailEvidenceIds.size).toBe(2);
    await korailFirstWatch.click();
    await expect(train9001.getByRole("button", { name: "특실 대기 취소" })).toBeVisible();

    await page.getByRole("button", { name: "이전" }).click();
    await page.getByRole("button", { name: "이전" }).click();
    await expect(page.getByRole("heading", { name: "어디로 떠나세요?" })).toBeVisible();
    await page.getByRole("checkbox", { name: /^SRT/ }).click();
    await page.getByRole("checkbox", { name: /^KTX/ }).click();
    await selectStation(page, "출발역", "수서");
    await selectStation(page, "도착역", "부산");
    await page.getByRole("button", { name: "다음", exact: true }).click();
    await expect(page.getByRole("heading", { name: "어떤 좌석을 찾을까요?" })).toBeVisible();
    await page.getByRole("button", { name: "다음", exact: true }).click();
    await expect(page.getByRole("heading", { name: "공식 시간표에서 관심 열차를 고르세요" }))
      .toBeVisible();

    const train9002 = page.locator("article.train-result-card").filter({ hasText: "9002" });
    const train9003 = page.locator("article.train-result-card").filter({ hasText: "9003" });
    await expect(train9002).toHaveCount(1);
    await expect(train9003).toHaveCount(1);
    await expect(train9002.getByText("매진", { exact: true })).toHaveCount(2);
    await expect(train9003.getByText("예약대기 가능", { exact: true })).toBeVisible();
    await expect.poll(() => srtEvidenceIds.size).toBeGreaterThanOrEqual(3);

    await train9002.getByRole("button", { name: "일반실 취소표 대기" }).click();
    await train9002.getByRole("button", { name: "특실 취소표 대기" }).click();
    await train9003.getByRole("button", { name: "일반실 예약대기" }).click();
    await expect(train9002.getByRole("button", { name: "일반실 대기 취소" }))
      .toBeVisible();
    await expect(train9002.getByRole("button", { name: "특실 대기 취소" }))
      .toBeVisible();
    await expect(train9003.getByRole("button", { name: "일반실 대기 취소" }))
      .toBeVisible();
    await expect.poll(() => watchRequestEvidenceIds.length).toBe(4);
    expect(korailEvidenceIds.has(watchRequestEvidenceIds[0] ?? "")).toBe(true);
    expect(watchRequestEvidenceIds.slice(1).every((id) => srtEvidenceIds.has(id))).toBe(true);
    await expect.poll(() => startStatuses.length).toBe(4);
    expect(startStatuses).toEqual(["scheduled", "scheduled", "scheduled", "scheduled"]);

    await expect(page.getByRole("button", { name: "등록 완료" })).toHaveCount(0);
    await expect(page.getByLabel("등록한 관심 열차 대기")).toHaveCount(0);
    await page.getByRole("button", { name: "홈", exact: true }).first().click();
    await expect(page.getByRole("heading", { name: "활동 중인 대기" })).toBeVisible();
    await expect(page.getByLabel("전체 4건 모두 표시 중")).toBeVisible();
    const first9001 = page.locator(".watch-row")
      .filter({ hasText: "9001" }).filter({ hasText: "특실" });
    const standard9002 = page.locator(".watch-row")
      .filter({ hasText: "9002" }).filter({ hasText: "일반실" });
    const first9002 = page.locator(".watch-row")
      .filter({ hasText: "9002" }).filter({ hasText: "특실" });
    const standard9003 = page.locator(".watch-row")
      .filter({ hasText: "9003" }).filter({ hasText: "일반실" });
    await expect(first9001).toContainText("감시 중", { timeout: 60_000 });
    await expect(first9001.locator(".watch-seat-evidence")).toHaveText(
      /^특실 · 매진 · 공식 관측 \d{2}:\d{2}$/,
    );
    await expect(standard9002).toContainText("좌석 발견", { timeout: 60_000 });
    await expect(first9002).toContainText("감시 중", { timeout: 60_000 });
    await expect(standard9003).toContainText("공식 예약대기", { timeout: 60_000 });
  });
});
