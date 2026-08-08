import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OfficialHandoff } from "../src/features/official-handoff/OfficialHandoff";
import {
  buildKorailNavigationIntentUrl,
  buildSrtMainIntentUrl,
  buildSrtTicketIntentUrl,
  isAndroidChromeUserAgent,
  isAndroidSamsungInternetUserAgent,
  KORAIL_OFFICIAL_ENTRY_URL,
  KORAIL_OFFICIAL_RESERVATION_URL,
  launchOfficialOpenTarget,
  resolveOfficialOpenTarget,
  resolveRailDeepLinkConfig,
  SRT_OFFICIAL_ENTRY_URL,
  SRT_OFFICIAL_RESERVATION_URL,
} from "../src/features/official-handoff/officialAppIntentUrl";
import type {
  OfficialWindowLike,
  RailDeepLinkConfig,
} from "../src/features/official-handoff/officialAppIntentUrl";

const ANDROID_CHROME_USER_AGENT =
  "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36";
const ANDROID_SAMSUNG_INTERNET_USER_AGENT =
  "Mozilla/5.0 (Linux; Android 15; SM-F966N) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/28.0 Chrome/130.0.0.0 Mobile Safari/537.36";
const ANDROID_WEBVIEW_USER_AGENT =
  "Mozilla/5.0 (Linux; Android 15; SM-F966N; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/140.0.0.0 Mobile Safari/537.36";

const VALIDATED_KORAIL_CONFIG: RailDeepLinkConfig = {
  korailBooking: {
    enabled: true,
    validatedAndroidAppVersion: "7.0.0+70000006",
  },
  korailTicket: { enabled: false, validatedAndroidAppVersion: null },
  srtMain: { enabled: false, validatedAndroidAppVersion: null },
  srtTicket: { enabled: false, validatedAndroidAppVersion: null },
};
const DISABLED_KORAIL_CONFIG: RailDeepLinkConfig = {
  korailBooking: { enabled: false, validatedAndroidAppVersion: null },
  korailTicket: { enabled: false, validatedAndroidAppVersion: null },
  srtMain: { enabled: false, validatedAndroidAppVersion: null },
  srtTicket: { enabled: false, validatedAndroidAppVersion: null },
};
const VALIDATED_ALL_CONFIG: RailDeepLinkConfig = {
  korailBooking: {
    enabled: true,
    validatedAndroidAppVersion: "7.0.0+70000006",
  },
  korailTicket: {
    enabled: true,
    validatedAndroidAppVersion: "7.0.0+70000006",
  },
  srtMain: {
    enabled: true,
    validatedAndroidAppVersion: "2.0.41+150",
  },
  srtTicket: {
    enabled: true,
    validatedAndroidAppVersion: "2.0.41+150",
  },
};

interface FakeOfficialWindow extends OfficialWindowLike {
  open: ReturnType<typeof vi.fn<OfficialWindowLike["open"]>>;
  location: { assign: ReturnType<typeof vi.fn<(url: string) => void>> };
  intentAnchor: HTMLAnchorElement;
  appendIntentAnchor: ReturnType<
    typeof vi.fn<(anchor: HTMLAnchorElement) => HTMLAnchorElement>
  >;
}

function fakeOfficialWindow(): FakeOfficialWindow {
  const intentAnchor = document.createElement("a");
  vi.spyOn(intentAnchor, "click").mockImplementation(() => undefined);
  vi.spyOn(intentAnchor, "remove").mockImplementation(() => undefined);
  const appendIntentAnchor = vi.fn<(anchor: HTMLAnchorElement) => HTMLAnchorElement>(
    (anchor) => anchor,
  );
  return {
    open: vi.fn<OfficialWindowLike["open"]>(),
    location: { assign: vi.fn<(url: string) => void>() },
    document: {
      createElement: vi.fn((_tagName: "a") => intentAnchor),
      body: { appendChild: appendIntentAnchor },
    },
    intentAnchor,
    appendIntentAnchor,
  };
}

describe("official Android app handoff", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
  });

  it("builds only the two evidence-backed Korail navigation intents", () => {
    expect(buildKorailNavigationIntentUrl("booking", KORAIL_OFFICIAL_ENTRY_URL)).toBe(
      "intent://navigation?view=booking#Intent;scheme=korailtalk;package=com.korail.talk;S.browser_fallback_url=https%3A%2F%2Fwww.korail.com%2Fticket%2Fsearch%2Fgeneral;end",
    );
    expect(buildKorailNavigationIntentUrl("ticket", KORAIL_OFFICIAL_RESERVATION_URL)).toBe(
      "intent://navigation?view=bookedTicket#Intent;scheme=korailtalk;package=com.korail.talk;S.browser_fallback_url=https%3A%2F%2Fwww.korail.com%2Fticket%2Freservation%2Flist;end",
    );
    expect(buildKorailNavigationIntentUrl("booking", KORAIL_OFFICIAL_RESERVATION_URL)).toBeNull();
    expect(buildKorailNavigationIntentUrl("ticket", KORAIL_OFFICIAL_ENTRY_URL)).toBeNull();
  });

  it("builds only the manifest-backed SRT main intent", () => {
    expect(buildSrtMainIntentUrl(SRT_OFFICIAL_ENTRY_URL)).toBe(
      "intent://main#Intent;scheme=srapp;package=kr.co.srail.newapp;S.browser_fallback_url=https%3A%2F%2Fetk.srail.kr%2Fhpg%2Fhra%2F01%2FselectScheduleList.do%3FpageId%3DTK0101010000;end",
    );
    expect(buildSrtMainIntentUrl(SRT_OFFICIAL_RESERVATION_URL)).toBeNull();
  });

  it("builds the evidence-backed SRT ticket intent with only the fixed string extra", () => {
    expect(buildSrtTicketIntentUrl(SRT_OFFICIAL_RESERVATION_URL)).toBe(
      "intent://main#Intent;scheme=srapp;package=kr.co.srail.newapp;S.btnNo=2;S.browser_fallback_url=https%3A%2F%2Fetk.srail.kr%2Fhpg%2Fhra%2F02%2FselectReservationList.do%3FpageId%3DTK0102010000;end",
    );
    expect(buildSrtTicketIntentUrl(SRT_OFFICIAL_ENTRY_URL)).toBeNull();
  });

  it("requires both an explicit flag and a recorded validated app version", () => {
    expect(resolveRailDeepLinkConfig("true", "7.0.0+70000006")).toEqual(
      VALIDATED_KORAIL_CONFIG,
    );
    expect(resolveRailDeepLinkConfig("true", "")).toEqual(DISABLED_KORAIL_CONFIG);
    expect(resolveRailDeepLinkConfig("false", "7.0.0+70000006")).toEqual({
      korailBooking: {
        enabled: false,
        validatedAndroidAppVersion: "7.0.0+70000006",
      },
      korailTicket: { enabled: false, validatedAndroidAppVersion: null },
      srtMain: { enabled: false, validatedAndroidAppVersion: null },
      srtTicket: { enabled: false, validatedAndroidAppVersion: null },
    });
    expect(resolveRailDeepLinkConfig(
      "true",
      "7.0.0+70000006",
      "true",
      "7.0.0+70000006",
      "true",
      "2.0.41+150",
      "true",
      "2.0.41+150",
    )).toEqual(VALIDATED_ALL_CONFIG);
    expect(resolveRailDeepLinkConfig(true, "7.0.0+70000006").korailBooking.enabled)
      .toBe(false);
  });

  it("recognizes supported Android browsers but excludes WebView", () => {
    expect(isAndroidChromeUserAgent(ANDROID_CHROME_USER_AGENT)).toBe(true);
    expect(isAndroidChromeUserAgent(ANDROID_SAMSUNG_INTERNET_USER_AGENT)).toBe(false);
    expect(isAndroidSamsungInternetUserAgent(ANDROID_SAMSUNG_INTERNET_USER_AGENT)).toBe(true);
    expect(isAndroidSamsungInternetUserAgent(ANDROID_WEBVIEW_USER_AGENT)).toBe(false);
    expect(isAndroidSamsungInternetUserAgent("Mozilla/5.0 SamsungBrowser/28.0")).toBe(false);
  });

  it("keeps Korail on HTTPS until the latest app version is explicitly validated", () => {
    expect(resolveOfficialOpenTarget(
      "KORAIL",
      KORAIL_OFFICIAL_ENTRY_URL,
      ANDROID_CHROME_USER_AGENT,
      "booking",
      DISABLED_KORAIL_CONFIG,
    )).toEqual({ url: KORAIL_OFFICIAL_ENTRY_URL, usesAndroidApp: false });

    expect(resolveOfficialOpenTarget(
      "KORAIL",
      KORAIL_OFFICIAL_ENTRY_URL,
      ANDROID_CHROME_USER_AGENT,
      "booking",
      VALIDATED_KORAIL_CONFIG,
    )).toEqual({
      url: buildKorailNavigationIntentUrl("booking", KORAIL_OFFICIAL_ENTRY_URL),
      usesAndroidApp: true,
    });
  });

  it("uses the booked-ticket route and reservation-list fallback for payment handoff", () => {
    expect(resolveOfficialOpenTarget(
      "KORAIL",
      KORAIL_OFFICIAL_ENTRY_URL,
      ANDROID_SAMSUNG_INTERNET_USER_AGENT,
      "ticket",
      VALIDATED_ALL_CONFIG,
    )).toEqual({
      url: buildKorailNavigationIntentUrl("ticket", KORAIL_OFFICIAL_RESERVATION_URL),
      usesAndroidApp: true,
    });
  });

  it("uses independently validated SRT booking and ticket handoffs", () => {
    expect(resolveOfficialOpenTarget(
      "SRT",
      "https://etk.srail.kr",
      ANDROID_SAMSUNG_INTERNET_USER_AGENT,
      "booking",
      VALIDATED_KORAIL_CONFIG,
    )).toEqual({ url: SRT_OFFICIAL_ENTRY_URL, usesAndroidApp: false });
    expect(resolveOfficialOpenTarget(
      "SRT",
      "https://etk.srail.kr",
      ANDROID_SAMSUNG_INTERNET_USER_AGENT,
      "booking",
      VALIDATED_ALL_CONFIG,
    )).toEqual({
      url: buildSrtMainIntentUrl(SRT_OFFICIAL_ENTRY_URL),
      usesAndroidApp: true,
    });
    expect(resolveOfficialOpenTarget(
      "SRT",
      "https://etk.srail.kr",
      ANDROID_CHROME_USER_AGENT,
      "ticket",
      VALIDATED_ALL_CONFIG,
    )).toEqual({
      url: buildSrtTicketIntentUrl(SRT_OFFICIAL_RESERVATION_URL),
      usesAndroidApp: true,
    });
  });

  it("keeps WebView and untrusted candidates on fail-closed paths", () => {
    expect(resolveOfficialOpenTarget(
      "KORAIL",
      KORAIL_OFFICIAL_ENTRY_URL,
      ANDROID_WEBVIEW_USER_AGENT,
      "booking",
      VALIDATED_KORAIL_CONFIG,
    )).toEqual({ url: KORAIL_OFFICIAL_ENTRY_URL, usesAndroidApp: false });
    expect(resolveOfficialOpenTarget(
      "KORAIL",
      "https://attacker.invalid/ticket",
      ANDROID_CHROME_USER_AGENT,
      "booking",
      VALIDATED_KORAIL_CONFIG,
    )).toBeNull();
  });

  it("uses an external anchor for a validated intent and a new window for HTTPS", () => {
    const intentWindow = fakeOfficialWindow();
    const intentUrl = buildKorailNavigationIntentUrl("booking", KORAIL_OFFICIAL_ENTRY_URL) ?? "";
    launchOfficialOpenTarget({
      url: intentUrl,
      usesAndroidApp: true,
    }, intentWindow);
    expect(intentWindow.intentAnchor.href).toBe(intentUrl);
    expect(intentWindow.intentAnchor.target).toBe("_blank");
    expect(intentWindow.intentAnchor.rel).toBe("noopener noreferrer");
    expect(intentWindow.intentAnchor.hidden).toBe(true);
    expect(intentWindow.appendIntentAnchor).toHaveBeenCalledWith(intentWindow.intentAnchor);
    expect(intentWindow.intentAnchor.click).toHaveBeenCalledOnce();
    expect(intentWindow.intentAnchor.remove).toHaveBeenCalledOnce();
    expect(intentWindow.location.assign).not.toHaveBeenCalled();
    expect(intentWindow.open).not.toHaveBeenCalled();

    const webWindow = fakeOfficialWindow();
    launchOfficialOpenTarget({ url: SRT_OFFICIAL_RESERVATION_URL, usesAndroidApp: false }, webWindow);
    expect(webWindow.open).toHaveBeenCalledWith(
      SRT_OFFICIAL_RESERVATION_URL,
      "_blank",
      "noopener,noreferrer",
    );
    expect(webWindow.location.assign).not.toHaveBeenCalled();
  });

  it("renders the Korail booking intent only when the build flag records validation", async () => {
    vi.stubEnv("VITE_KORAIL_BOOKING_DEEPLINK_ENABLED", "true");
    vi.stubEnv("VITE_KORAIL_BOOKING_VALIDATED_VERSION", "7.0.0+70000006");
    vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue(ANDROID_CHROME_USER_AGENT);
    const user = userEvent.setup();
    render(<OfficialHandoff
      train={{
        id: "KORAIL:85:2026-08-06T14:11:00+09:00",
        provider: "KORAIL",
        name: "KTX 085",
        origin: "대전",
        destination: "서울",
        departure: "14:11",
      }}
      onCopy={vi.fn()}
    />);

    await user.click(screen.getByRole("button", { name: /공식 좌석 확인 전 안내 열기/ }));
    const dialog = screen.getByRole("dialog");
    const link = within(dialog).getByRole("link", { name: /공식 앱 또는 홈페이지 열기/ });
    expect(link.getAttribute("href")).toBe(
      buildKorailNavigationIntentUrl("booking", KORAIL_OFFICIAL_ENTRY_URL),
    );
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("keeps SRT on a new official web window when only Korail deep linking is enabled", async () => {
    vi.stubEnv("VITE_KORAIL_BOOKING_DEEPLINK_ENABLED", "true");
    vi.stubEnv("VITE_KORAIL_BOOKING_VALIDATED_VERSION", "7.0.0+70000006");
    vi.spyOn(window.navigator, "userAgent", "get")
      .mockReturnValue(ANDROID_SAMSUNG_INTERNET_USER_AGENT);
    const openWindow = vi.spyOn(window, "open").mockImplementation(() => null);
    const user = userEvent.setup();
    render(<OfficialHandoff
      train={{
        id: "SRT:369:2026-08-08T20:00:00+09:00",
        provider: "SRT",
        name: "SRT 369",
        origin: "수서",
        destination: "대전",
        departure: "20:00",
      }}
      onCopy={vi.fn()}
    />);

    await user.click(screen.getByRole("button", { name: /공식 좌석 확인 전 안내 열기/ }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /공식 페이지 열기/ }));
    expect(openWindow).toHaveBeenCalledWith(
      SRT_OFFICIAL_ENTRY_URL,
      "_blank",
      "noopener,noreferrer",
    );
    expect(within(dialog).queryByRole("link", { name: /공식 앱 또는 홈페이지 열기/ })).toBeNull();
  });

  it("renders the SRT main intent only when its validated build flag is enabled", async () => {
    vi.stubEnv("VITE_SRT_MAIN_DEEPLINK_ENABLED", "true");
    vi.stubEnv("VITE_SRT_MAIN_VALIDATED_VERSION", "2.0.41+150");
    vi.spyOn(window.navigator, "userAgent", "get")
      .mockReturnValue(ANDROID_SAMSUNG_INTERNET_USER_AGENT);
    const user = userEvent.setup();
    render(<OfficialHandoff
      train={{
        id: "SRT:369:2026-08-08T20:00:00+09:00",
        provider: "SRT",
        name: "SRT 369",
        origin: "수서",
        destination: "대전",
        departure: "20:00",
      }}
      onCopy={vi.fn()}
    />);

    await user.click(screen.getByRole("button", { name: /공식 좌석 확인 전 안내 열기/ }));
    const dialog = screen.getByRole("dialog");
    const link = within(dialog).getByRole("link", { name: /공식 앱 또는 홈페이지 열기/ });
    expect(link.getAttribute("href")).toBe(buildSrtMainIntentUrl(SRT_OFFICIAL_ENTRY_URL));
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  });
});
