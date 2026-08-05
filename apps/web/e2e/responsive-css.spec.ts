import { expect, test, type Locator, type Page, type Route } from "@playwright/test";

interface ViewportCase {
  name: string;
  width: number;
  height: number;
}

interface BrowserTelemetry {
  handledApiPaths: Set<string>;
  unhandledApiRequests: string[];
  consoleErrors: string[];
  pageErrors: string[];
}

const viewportCases: readonly ViewportCase[] = [
  { name: "desktop", width: 1440, height: 1000 },
  { name: "320px mobile", width: 320, height: 844 },
  { name: "200% zoom equivalent", width: 720, height: 500 },
];

const expectedHomeApiPaths = [
  "/api/v1/auth/status",
  "/api/v1/events",
  "/api/v1/notifications/channels",
  "/api/v1/preferences/ui",
  "/api/v1/provider-accounts",
  "/api/v1/provider-runtime-status",
  "/api/v1/watches",
] as const;

function timestamp(offsetMilliseconds = 0): string {
  return new Date(Date.now() + offsetMilliseconds).toISOString();
}

function activeWatch(): Record<string, unknown> {
  const observedAt = timestamp(-5_000);
  const evidenceCreatedAt = timestamp(-10_000);
  return {
    id: "responsive-watch",
    provider: "korail",
    origin: "서울",
    destination: "부산",
    travel_date: "2026-08-08",
    time_from: "12:00:00",
    time_to: "15:00:00",
    train_numbers: ["KTX 101"],
    seat_class: "standard",
    status: "seat_found",
    reservation_policy: "reserve_once_before_payment",
    last_checked_at: observedAt,
    created_at: timestamp(-60_000),
    updated_at: observedAt,
    official_booking_url: "https://www.korail.com/ticket/search/general",
    candidates: [
      {
        id: "responsive-candidate",
        train_number: "KTX 101",
        departure_at: "2026-08-08T12:00:00+09:00",
        arrival_at: "2026-08-08T14:30:00+09:00",
        seat_class: "standard",
        priority: 1,
        registration_evidence: {
          id: "10000000-0000-4000-8000-000000000101",
          status: "sold_out",
          provenance: {
            kind: "official_provider",
            source: "responsive-e2e",
            observed_at: observedAt,
          },
          created_at: evidenceCreatedAt,
          registration_valid_until: timestamp(5 * 60_000),
        },
        latest_observation: {
          status: "available",
          source: "korail-browser",
          observed_at: observedAt,
          fresh_until: timestamp(5 * 60_000),
        },
      },
    ],
  };
}

function elapsedPaymentWatch(): Record<string, unknown> {
  return {
    id: "responsive-payment-watch",
    provider: "korail",
    origin: "서울",
    destination: "부산",
    travel_date: "2026-08-08",
    time_from: "10:00:00",
    time_to: "13:00:00",
    train_numbers: ["KTX 099"],
    seat_class: "standard",
    status: "payment_required",
    reservation_policy: "reserve_once_before_payment",
    payment_deadline: timestamp(-60_000),
    created_at: timestamp(-120_000),
    updated_at: timestamp(-60_000),
    official_booking_url: "https://www.korail.com/ticket/search/general",
    candidates: [
      {
        id: "responsive-payment-candidate",
        train_number: "KTX 099",
        departure_at: "2026-08-08T10:00:00+09:00",
        arrival_at: "2026-08-08T12:30:00+09:00",
        seat_class: "standard",
        priority: 1,
        state: "payment_required",
      },
    ],
  };
}

function json(route: Route, body: unknown, status = 200): Promise<void> {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

function observeBrowser(page: Page): BrowserTelemetry {
  const telemetry: BrowserTelemetry = {
    handledApiPaths: new Set<string>(),
    unhandledApiRequests: [],
    consoleErrors: [],
    pageErrors: [],
  };
  page.on("console", (message) => {
    if (message.type() === "error") telemetry.consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => telemetry.pageErrors.push(error.message));
  return telemetry;
}

async function installMockApi(page: Page, telemetry: BrowserTelemetry): Promise<void> {
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;

    if (path.endsWith("/events")) {
      telemetry.handledApiPaths.add(path);
      await route.fulfill({ status: 204, body: "" });
      return;
    }
    if (path.endsWith("/auth/status")) {
      telemetry.handledApiPaths.add(path);
      await json(route, {
        configured: true,
        authenticated: true,
        registration_allowed: false,
      });
      return;
    }
    if (path.endsWith("/notifications/channels")) {
      telemetry.handledApiPaths.add(path);
      await json(route, []);
      return;
    }
    if (path.endsWith("/provider-accounts")) {
      telemetry.handledApiPaths.add(path);
      await json(route, [{
        provider: "KORAIL",
        configured: true,
        enabled: true,
        login_method: "membership_number",
        masked_login_id: "12******90",
        credential_version: 3,
        last_auth_status: "authenticated",
        last_authenticated_at: timestamp(-60_000),
        updated_at: timestamp(-60_000),
      }]);
      return;
    }
    if (path.endsWith("/provider-runtime-status")) {
      telemetry.handledApiPaths.add(path);
      await json(route, [
        {
          provider: "KORAIL",
          state: "ready",
          credential_generation: "3",
          created_age_seconds: 120,
          last_verified_age_seconds: 60,
          last_used_age_seconds: 10,
          local_reuse_remaining_seconds: 240,
          locally_reusable: true,
          prewarm_outcome: "authenticated",
        },
        {
          provider: "SRT",
          state: "cold",
          credential_generation: null,
          created_age_seconds: null,
          last_verified_age_seconds: null,
          last_used_age_seconds: null,
          local_reuse_remaining_seconds: null,
          locally_reusable: false,
          prewarm_outcome: null,
        },
      ]);
      return;
    }
    if (path.endsWith("/preferences/ui")) {
      telemetry.handledApiPaths.add(path);
      await json(route, {
        timetable_refresh_interval_seconds: 30,
        seat_observation_interval_seconds: 5,
        preferences_updated_at: timestamp(-60_000),
      });
      return;
    }
    if (path.endsWith("/watches") && request.method() === "GET") {
      telemetry.handledApiPaths.add(path);
      await json(route, [activeWatch(), elapsedPaymentWatch()]);
      return;
    }

    telemetry.unhandledApiRequests.push(`${request.method()} ${path}`);
    await json(route, { detail: `unhandled responsive E2E route: ${path}` }, 500);
  });
}

async function expectWithinViewport(
  locator: Locator,
  viewportWidth: number,
  regionLabel = "layout region",
): Promise<void> {
  await expect(locator).toBeVisible();
  const metrics = await locator.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return {
      clientWidth: element.clientWidth,
      scrollWidth: element.scrollWidth,
      left: rect.left,
      right: rect.right,
    };
  });
  expect(metrics.clientWidth, `${regionLabel} must have a usable width`).toBeGreaterThan(0);
  expect(
    metrics.scrollWidth,
    `${regionLabel} must not contain clipped horizontal overflow`,
  ).toBeLessThanOrEqual(metrics.clientWidth + 1);
  expect(metrics.left, `${regionLabel} must not extend past the left viewport edge`)
    .toBeGreaterThanOrEqual(-0.5);
  expect(
    metrics.right,
    `${regionLabel} must not extend past the right viewport edge`,
  ).toBeLessThanOrEqual(viewportWidth + 0.5);
}

async function expectVisibleActionTarget(locator: Locator, label: string): Promise<void> {
  await expect(locator, `${label} must be visible`).toBeVisible();
  const box = await locator.boundingBox();
  expect(box, `${label} must have a bounding box`).not.toBeNull();
  if (box === null) return;

  expect(box.width, `${label} width must be at least 44px`).toBeGreaterThanOrEqual(44);
  expect(box.height, `${label} height must be at least 44px`).toBeGreaterThanOrEqual(44);
}

async function expectCoreActionTargets(page: Page): Promise<void> {
  const actions = page.locator([
    ".active-refresh-status .icon-button",
    ".active-history-link",
    ".button-new-wide",
    ".watch-policy-switch",
    ".watch-booking-button",
    ".watch-control-actions .icon-button",
    ".bottom-item",
  ].join(", "));
  const count = await actions.count();
  const visibleBoxes = [];

  for (let index = 0; index < count; index += 1) {
    const action = actions.nth(index);
    if (!(await action.isVisible())) continue;
    const box = await action.boundingBox();
    expect(box, "visible core action must have a bounding box").not.toBeNull();
    if (box !== null) visibleBoxes.push(box);
  }

  expect(visibleBoxes.length, "all six core home actions must be present and visible").toBeGreaterThanOrEqual(6);
  for (const box of visibleBoxes) {
    expect(box.width, "core action width must be at least 44px").toBeGreaterThanOrEqual(44);
    expect(box.height, "core action height must be at least 44px").toBeGreaterThanOrEqual(44);
  }
}

async function expectOfficialHandoffWithinBounds(
  page: Page,
  viewport: ViewportCase,
): Promise<void> {
  const trigger = page.locator(".watch-booking-button");
  await trigger.click();

  const dialog = page.getByRole("dialog", { name: /공식 예매 안내/ });
  const panel = page.locator(".official-handoff-panel");
  await expect(dialog).toBeVisible();
  await expectWithinViewport(panel, viewport.width);

  const panelBox = await panel.boundingBox();
  expect(panelBox, "official handoff panel must have a bounding box").not.toBeNull();
  if (panelBox !== null) {
    expect(panelBox.y).toBeGreaterThanOrEqual(-0.5);
    expect(panelBox.y + panelBox.height).toBeLessThanOrEqual(viewport.height + 0.5);
  }

  await expectVisibleActionTarget(
    dialog.getByRole("button", { name: /^공식 (?:예매 안내|좌석 확인 안내) 닫기$/ }),
    "official handoff close action",
  );
  await expectVisibleActionTarget(
    dialog.getByRole("button", { name: "여정 복사" }),
    "official handoff copy action",
  );
  await expectVisibleActionTarget(
    dialog.getByRole("button", { name: /공식 페이지 열기/ }),
    "official handoff provider action",
  );

  const appShell = page.locator(".app-shell");
  await expect(appShell).toHaveJSProperty("inert", true);
  const rootWidths = await page.evaluate(() => ({
    documentClient: document.documentElement.clientWidth,
    documentScroll: document.documentElement.scrollWidth,
    bodyClient: document.body.clientWidth,
    bodyScroll: document.body.scrollWidth,
  }));
  expect(rootWidths.documentScroll).toBeLessThanOrEqual(rootWidths.documentClient);
  expect(rootWidths.bodyScroll).toBeLessThanOrEqual(rootWidths.bodyClient);

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
  await expect(appShell).toHaveJSProperty("inert", false);
}

async function expectReservationsWithinBounds(
  page: Page,
  viewport: ViewportCase,
): Promise<void> {
  const navigation = page.locator(
    viewport.width <= 720 ? ".bottom-nav .bottom-item" : ".side-nav .nav-item",
  ).filter({ hasText: "내 예약" });
  await navigation.click();

  await expect(page.getByRole("heading", { name: "내 예약" })).toBeVisible();
  for (const selector of [
    ".page",
    ".reservation-summary",
    ".reservation-list",
  ]) {
    await expectWithinViewport(page.locator(selector), viewport.width, selector);
  }
  const reservationItems = page.locator(".reservation-item");
  await expect(reservationItems).toHaveCount(2);
  for (let index = 0; index < await reservationItems.count(); index += 1) {
    await expectWithinViewport(reservationItems.nth(index), viewport.width);
  }
  await expectWithinViewport(page.locator(".reservation-payment-deadline"), viewport.width);
  await expectVisibleActionTarget(
    page.locator(".reservation-item > .button").filter({ hasText: "공식 확인 열기" }),
    "elapsed payment official action",
  );
  const createAction = viewport.width <= 720
    ? page.locator(".bottom-nav .bottom-item").filter({ hasText: "새 대기" })
    : page.getByRole("main").getByRole("button", { name: "새 대기" });
  await expectVisibleActionTarget(createAction, "reservation create action");

  const summaryCards = page.locator(".reservation-summary > div");
  expect(await summaryCards.count()).toBeGreaterThanOrEqual(3);
  for (let index = 0; index < await summaryCards.count(); index += 1) {
    await expectWithinViewport(summaryCards.nth(index), viewport.width);
  }

  const rootWidths = await page.evaluate(() => ({
    documentClient: document.documentElement.clientWidth,
    documentScroll: document.documentElement.scrollWidth,
    bodyClient: document.body.clientWidth,
    bodyScroll: document.body.scrollWidth,
  }));
  expect(rootWidths.documentScroll).toBeLessThanOrEqual(rootWidths.documentClient);
  expect(rootWidths.bodyScroll).toBeLessThanOrEqual(rootWidths.bodyClient);
}

async function expectRefreshPreferencesWithinBounds(
  page: Page,
  viewport: ViewportCase,
): Promise<void> {
  const settingsNavigation = page.locator(
    viewport.width <= 720 ? ".bottom-nav .bottom-item" : ".side-nav .nav-item",
  ).filter({ hasText: "설정" });
  await settingsNavigation.click();
  await page.getByRole("navigation", { name: "설정 메뉴" })
    .getByRole("button", { name: "화면 동작" })
    .click();

  await expect(page.getByRole("heading", { name: "화면 동작" })).toBeVisible();
  for (const selector of [
    ".settings-panel",
    ".refresh-preference-card",
    ".refresh-preference-fields",
    ".refresh-preference-actions",
  ]) {
    await expectWithinViewport(page.locator(selector), viewport.width, selector);
  }

  const inputs = page.locator(".refresh-preference-input");
  const inputControls = page.locator(".refresh-preference-input input");
  await expect(inputs).toHaveCount(2);
  await expect(inputControls).toHaveCount(2);
  for (let index = 0; index < 2; index += 1) {
    await expectWithinViewport(inputs.nth(index), viewport.width);
    await expectWithinViewport(inputControls.nth(index), viewport.width);
    await expectVisibleActionTarget(inputs.nth(index), `refresh preference input ${index + 1}`);
  }
  const saveAction = page.getByRole("button", { name: "간격 저장" });
  await expectWithinViewport(saveAction, viewport.width);
  await expectVisibleActionTarget(
    saveAction,
    "refresh preference save action",
  );
}

async function expectWatchRegionsDoNotOverlap(page: Page): Promise<void> {
  const regionSelectors = [
    ".watch-provider",
    ".watch-time",
    ".watch-state",
    ".row-actions",
  ] as const;
  const regions = [];

  for (const selector of regionSelectors) {
    const locator = page.locator(selector);
    await expect(locator).toBeVisible();
    const box = await locator.boundingBox();
    expect(box, `${selector} must have a bounding box`).not.toBeNull();
    if (box !== null) regions.push({ selector, box });
  }

  for (let leftIndex = 0; leftIndex < regions.length; leftIndex += 1) {
    for (let rightIndex = leftIndex + 1; rightIndex < regions.length; rightIndex += 1) {
      const left = regions[leftIndex];
      const right = regions[rightIndex];
      if (left === undefined || right === undefined) continue;

      const horizontalIntersection = Math.min(
        left.box.x + left.box.width,
        right.box.x + right.box.width,
      ) - Math.max(left.box.x, right.box.x);
      const verticalIntersection = Math.min(
        left.box.y + left.box.height,
        right.box.y + right.box.height,
      ) - Math.max(left.box.y, right.box.y);
      expect(
        horizontalIntersection <= 0.5 || verticalIntersection <= 0.5,
        `${left.selector} and ${right.selector} must not overlap`,
      ).toBe(true);
    }
  }
}

async function expectHomeApiAndBrowserClean(telemetry: BrowserTelemetry): Promise<void> {
  await expect.poll(() => expectedHomeApiPaths.every((path) => (
    telemetry.handledApiPaths.has(path)
  ))).toBe(true);
  expect([...telemetry.handledApiPaths].sort()).toEqual([...expectedHomeApiPaths].sort());
  expect(telemetry.unhandledApiRequests).toEqual([]);
  expect(telemetry.consoleErrors).toEqual([]);
  expect(telemetry.pageErrors).toEqual([]);
}

async function expectMobileReadingOrder(page: Page, viewportHeight: number): Promise<void> {
  const regions = await page.evaluate(() => {
    const rect = (selector: string): DOMRect => {
      const element = document.querySelector(selector);
      if (!(element instanceof HTMLElement)) throw new Error(`missing layout region: ${selector}`);
      return element.getBoundingClientRect();
    };
    const mobileHeader = rect(".mobile-header");
    const pageHeader = rect(".page-header");
    const hero = rect(".watch-management-hero");
    const activeSection = rect(".active-section");
    const sectionHeading = rect(".active-section .section-heading");
    const watchRow = rect(".watch-row");
    const bottomNav = rect(".bottom-nav");
    return {
      mobileHeader: { top: mobileHeader.top, bottom: mobileHeader.bottom },
      pageHeader: { top: pageHeader.top, bottom: pageHeader.bottom },
      hero: { top: hero.top, bottom: hero.bottom },
      activeSection: { top: activeSection.top, bottom: activeSection.bottom },
      sectionHeading: { top: sectionHeading.top, bottom: sectionHeading.bottom },
      watchRow: { top: watchRow.top, bottom: watchRow.bottom },
      bottomNav: { top: bottomNav.top, bottom: bottomNav.bottom },
    };
  });

  expect(regions.mobileHeader.top).toBeGreaterThanOrEqual(0);
  expect(regions.mobileHeader.bottom).toBeLessThanOrEqual(regions.pageHeader.top + 0.5);
  expect(regions.pageHeader.bottom).toBeLessThanOrEqual(regions.hero.top + 0.5);
  expect(regions.hero.bottom).toBeLessThanOrEqual(regions.activeSection.top + 0.5);
  expect(regions.sectionHeading.bottom).toBeLessThanOrEqual(regions.watchRow.top + 0.5);
  expect(regions.bottomNav.top).toBeGreaterThan(regions.mobileHeader.bottom);
  expect(regions.bottomNav.bottom).toBeLessThanOrEqual(viewportHeight + 0.5);

  const lastAction = page.locator(".button-new-wide");
  await lastAction.scrollIntoViewIfNeeded();
  const actionBox = await lastAction.boundingBox();
  const bottomNavBox = await page.locator(".bottom-nav").boundingBox();
  expect(actionBox).not.toBeNull();
  expect(bottomNavBox).not.toBeNull();
  if (actionBox === null || bottomNavBox === null) return;
  expect(actionBox.y).toBeGreaterThanOrEqual(-0.5);
  expect(actionBox.y + actionBox.height).toBeLessThanOrEqual(bottomNavBox.y + 0.5);
  expect(actionBox.y + actionBox.height).toBeLessThanOrEqual(viewportHeight + 0.5);
}

for (const viewport of viewportCases) {
  test(`${viewport.name} keeps the active-watch home layout within bounds`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const telemetry = observeBrowser(page);
    await installMockApi(page, telemetry);
    await page.goto("/");

    await expect(page.getByRole("heading", { name: "활동 중인 대기" })).toBeVisible();
    await expect(page.locator(".watch-row")).toHaveCount(1);

    const rootWidths = await page.evaluate(() => ({
      documentClient: document.documentElement.clientWidth,
      documentScroll: document.documentElement.scrollWidth,
      bodyClient: document.body.clientWidth,
      bodyScroll: document.body.scrollWidth,
    }));
    expect(rootWidths.documentScroll).toBeLessThanOrEqual(rootWidths.documentClient);
    expect(rootWidths.bodyScroll).toBeLessThanOrEqual(rootWidths.bodyClient);

    for (const selector of [
      ".app-shell",
      ".main-content",
      ".page",
      ".watch-list",
      ".watch-row",
      ".row-actions",
      ".watch-provider",
      ".watch-provider > div",
      ".watch-time",
      ".watch-state",
      ".watch-seat-evidence",
      ".watch-policy-control",
      ".watch-policy-label",
      ".watch-policy-switch",
      ".watch-booking-action",
      ".watch-booking-button",
      ".watch-control-actions",
    ]) {
      await expectWithinViewport(page.locator(selector), viewport.width);
    }

    await expect(page.locator(".watch-policy-label")).toHaveText("좌석 재발견마다 자동 예매");
    await expectVisibleActionTarget(page.locator(".watch-booking-button"), "official booking action");
    await expectCoreActionTargets(page);
    await expectWatchRegionsDoNotOverlap(page);
    if (viewport.width <= 720) {
      const bottomItems = page.locator(".bottom-item");
      await expect(bottomItems).toHaveCount(4);
      for (let index = 0; index < 4; index += 1) {
        await expectVisibleActionTarget(bottomItems.nth(index), `bottom navigation item ${index + 1}`);
      }
      await expectMobileReadingOrder(page, viewport.height);
    }
    await expectOfficialHandoffWithinBounds(page, viewport);
    await expectReservationsWithinBounds(page, viewport);
    await expectRefreshPreferencesWithinBounds(page, viewport);
    await expectHomeApiAndBrowserClean(telemetry);
  });
}
