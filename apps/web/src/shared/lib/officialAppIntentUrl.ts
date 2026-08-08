export const KORAIL_OFFICIAL_ENTRY_URL =
  "https://www.korail.com/ticket/search/general";
export const KORAIL_OFFICIAL_RESERVATION_URL =
  "https://www.korail.com/ticket/reservation/list";
export const SRT_OFFICIAL_ENTRY_URL =
  "https://etk.srail.kr/hpg/hra/01/selectScheduleList.do?pageId=TK0101010000";
export const SRT_OFFICIAL_RESERVATION_URL =
  "https://etk.srail.kr/hpg/hra/02/selectReservationList.do?pageId=TK0102010000";

interface OfficialProviderWebConfig {
  bookingUrl: string;
  reservationUrl: string;
  allowedHosts: ReadonlySet<string>;
}

const OFFICIAL_PROVIDER_WEB: Readonly<Record<string, OfficialProviderWebConfig>> = {
  KORAIL: {
    bookingUrl: KORAIL_OFFICIAL_ENTRY_URL,
    reservationUrl: KORAIL_OFFICIAL_RESERVATION_URL,
    allowedHosts: new Set(["www.korail.com", "www.letskorail.com"]),
  },
  SRT: {
    bookingUrl: SRT_OFFICIAL_ENTRY_URL,
    reservationUrl: SRT_OFFICIAL_RESERVATION_URL,
    allowedHosts: new Set(["etk.srail.kr"]),
  },
};

const KORAIL_ANDROID_PACKAGE = "com.korail.talk";
const KORAIL_NAVIGATION_SCHEME = "korailtalk";
const KORAIL_NAVIGATION_VIEW: Readonly<Record<OfficialHandoffDestination, string>> = {
  booking: "booking",
  ticket: "bookedTicket",
};
const SRT_ANDROID_PACKAGE = "kr.co.srail.newapp";
const SRT_MAIN_SCHEME = "srapp";

export type OfficialHandoffDestination = "booking" | "ticket";

export type AndroidDeepLinkValidation =
  | {
    enabled: false;
    validatedAndroidAppVersion: string | null;
  }
  | {
    enabled: true;
    validatedAndroidAppVersion: string;
  };

export interface RailDeepLinkConfig {
  korailBooking: AndroidDeepLinkValidation;
  korailTicket: AndroidDeepLinkValidation;
  srtMain: AndroidDeepLinkValidation;
  srtTicket: AndroidDeepLinkValidation;
}

function resolveAndroidDeepLinkValidation(
  enabledCandidate: unknown,
  versionCandidate: unknown,
): AndroidDeepLinkValidation {
  const validatedAndroidAppVersion = typeof versionCandidate === "string"
    && versionCandidate.trim().length > 0
    && versionCandidate.trim().length <= 64
    ? versionCandidate.trim()
    : null;
  if (enabledCandidate === "true" && validatedAndroidAppVersion !== null) {
    return { enabled: true, validatedAndroidAppVersion };
  }
  return { enabled: false, validatedAndroidAppVersion };
}

export function resolveRailDeepLinkConfig(
  korailBookingEnabledCandidate: unknown,
  korailBookingVersionCandidate: unknown,
  korailTicketEnabledCandidate: unknown = undefined,
  korailTicketVersionCandidate: unknown = undefined,
  srtEnabledCandidate: unknown = undefined,
  srtVersionCandidate: unknown = undefined,
  srtTicketEnabledCandidate: unknown = undefined,
  srtTicketVersionCandidate: unknown = undefined,
): RailDeepLinkConfig {
  return {
    korailBooking: resolveAndroidDeepLinkValidation(
      korailBookingEnabledCandidate,
      korailBookingVersionCandidate,
    ),
    korailTicket: resolveAndroidDeepLinkValidation(
      korailTicketEnabledCandidate,
      korailTicketVersionCandidate,
    ),
    srtMain: resolveAndroidDeepLinkValidation(
      srtEnabledCandidate,
      srtVersionCandidate,
    ),
    srtTicket: resolveAndroidDeepLinkValidation(
      srtTicketEnabledCandidate,
      srtTicketVersionCandidate,
    ),
  };
}

function browserRailDeepLinkConfig(): RailDeepLinkConfig {
  return resolveRailDeepLinkConfig(
    import.meta.env.VITE_KORAIL_BOOKING_DEEPLINK_ENABLED,
    import.meta.env.VITE_KORAIL_BOOKING_VALIDATED_VERSION,
    import.meta.env.VITE_KORAIL_TICKET_DEEPLINK_ENABLED,
    import.meta.env.VITE_KORAIL_TICKET_VALIDATED_VERSION,
    import.meta.env.VITE_SRT_MAIN_DEEPLINK_ENABLED,
    import.meta.env.VITE_SRT_MAIN_VALIDATED_VERSION,
    import.meta.env.VITE_SRT_TICKET_DEEPLINK_ENABLED,
    import.meta.env.VITE_SRT_TICKET_VALIDATED_VERSION,
  );
}

export type OfficialOpenTarget =
  | { url: string; usesAndroidApp: true }
  | { url: string; usesAndroidApp: false };

export interface OfficialWindowLike {
  open: (url: string, target: "_blank", features: "noopener,noreferrer") => unknown;
  location: { assign: (url: string) => void };
  document?: {
    createElement: (tagName: "a") => HTMLAnchorElement;
    body: {
      appendChild: (anchor: HTMLAnchorElement) => HTMLAnchorElement;
    };
  };
}

// 설치형 Chrome PWA에서는 사용자 클릭 안에서 target=_blank인 실제 anchor를 눌러야
// 설치 앱과 외부 Custom Tab fallback을 모두 유지할 수 있다. 현재 창 location.assign은
// 미설치 시 PWA 자체를 공식 웹으로 교체하므로 브라우저 document가 없는 테스트 대역의
// 최후 fallback으로만 남긴다.
export function launchOfficialOpenTarget(
  target: OfficialOpenTarget,
  windowLike: OfficialWindowLike = window,
): void {
  if (target.usesAndroidApp) {
    if (!windowLike.document?.body) {
      windowLike.location.assign(target.url);
      return;
    }
    const anchor = windowLike.document.createElement("a");
    anchor.href = target.url;
    anchor.target = "_blank";
    anchor.rel = "noopener noreferrer";
    anchor.hidden = true;
    windowLike.document.body.appendChild(anchor);
    try {
      anchor.click();
    } finally {
      anchor.remove();
    }
    return;
  }
  windowLike.open(target.url, "_blank", "noopener,noreferrer");
}

export function isAndroidChromeUserAgent(candidate: unknown): boolean {
  if (typeof candidate !== "string") return false;
  return /Android/i.test(candidate)
    && /Chrome\/\d/i.test(candidate)
    && !/(?:EdgA|OPR|SamsungBrowser|Firefox|; wv\))/i.test(candidate);
}

export function isAndroidSamsungInternetUserAgent(candidate: unknown): boolean {
  if (typeof candidate !== "string") return false;
  return /Android/i.test(candidate)
    && /SamsungBrowser\/\d/i.test(candidate)
    && !/; wv\)/i.test(candidate);
}

// 클릭 기반 intent와 고정 HTTPS fallback 처리가 문서화된 Android Chrome·삼성 인터넷만
// 앱 열기 후보로 둔다. 실제 생성은 운영사별 BROWSABLE 검증 플래그까지 충족해야 한다.
// WebView 기반 인앱 브라우저는 intent 차단·무반응이 흔해 공식 HTTPS를 유지한다.
function supportsOfficialAndroidAppIntent(userAgent: unknown): boolean {
  return isAndroidChromeUserAgent(userAgent)
    || isAndroidSamsungInternetUserAgent(userAgent);
}

export function buildKorailNavigationIntentUrl(
  destination: OfficialHandoffDestination,
  fallbackCandidate: unknown,
): string | null {
  const expectedFallback = destination === "ticket"
    ? KORAIL_OFFICIAL_RESERVATION_URL
    : KORAIL_OFFICIAL_ENTRY_URL;
  if (fallbackCandidate !== expectedFallback) return null;
  return `intent://navigation?view=${KORAIL_NAVIGATION_VIEW[destination]}#Intent;scheme=${KORAIL_NAVIGATION_SCHEME};package=${KORAIL_ANDROID_PACKAGE};S.browser_fallback_url=${encodeURIComponent(expectedFallback)};end`;
}

export function buildSrtMainIntentUrl(fallbackCandidate: unknown): string | null {
  if (fallbackCandidate !== SRT_OFFICIAL_ENTRY_URL) return null;
  return `intent://main#Intent;scheme=${SRT_MAIN_SCHEME};package=${SRT_ANDROID_PACKAGE};S.browser_fallback_url=${encodeURIComponent(SRT_OFFICIAL_ENTRY_URL)};end`;
}

export function buildSrtTicketIntentUrl(fallbackCandidate: unknown): string | null {
  if (fallbackCandidate !== SRT_OFFICIAL_RESERVATION_URL) return null;
  // SRT 2.0.41의 exported srapp://main 진입점은 문자열 extra btnNo=2를
  // SRWebActivity로 전달해 승차권 확인 화면을 연다. 웜 실행의 기존 WebView 목록을
  // 강제로 갱신하는 계약은 아니므로 사용자·여정 데이터나 추측 extra를 추가하지 않는다.
  return `intent://main#Intent;scheme=${SRT_MAIN_SCHEME};package=${SRT_ANDROID_PACKAGE};S.btnNo=2;S.browser_fallback_url=${encodeURIComponent(SRT_OFFICIAL_RESERVATION_URL)};end`;
}

function browserUserAgent(): string {
  return typeof navigator === "undefined" ? "" : navigator.userAgent;
}

function isAllowedOfficialCandidate(
  officialProvider: OfficialProviderWebConfig,
  candidate: unknown,
): boolean {
  if (typeof candidate !== "string") return false;
  try {
    const url = new URL(candidate);
    return url.protocol === "https:"
      && !url.username
      && !url.password
      && officialProvider.allowedHosts.has(url.hostname.toLowerCase());
  } catch {
    return false;
  }
}

export function resolveOfficialOpenTarget(
  provider: unknown,
  officialUrlCandidate: unknown,
  userAgent: unknown = browserUserAgent(),
  destination: OfficialHandoffDestination = "booking",
  deepLinkConfig: RailDeepLinkConfig = browserRailDeepLinkConfig(),
): OfficialOpenTarget | null {
  if (typeof provider !== "string") return null;
  const normalizedProvider = provider.toUpperCase();
  const officialProvider = OFFICIAL_PROVIDER_WEB[normalizedProvider];
  if (!officialProvider || !isAllowedOfficialCandidate(officialProvider, officialUrlCandidate)) {
    return null;
  }

  const fallbackUrl = destination === "ticket"
    ? officialProvider.reservationUrl
    : officialProvider.bookingUrl;
  const supportsAppIntent = supportsOfficialAndroidAppIntent(userAgent);
  let intentUrl: string | null = null;
  if (
    normalizedProvider === "KORAIL"
    && (destination === "ticket"
      ? deepLinkConfig.korailTicket.enabled
      : deepLinkConfig.korailBooking.enabled)
    && supportsAppIntent
  ) {
    intentUrl = buildKorailNavigationIntentUrl(destination, fallbackUrl);
  } else if (
    normalizedProvider === "SRT"
    && destination === "booking"
    && deepLinkConfig.srtMain.enabled
    && supportsAppIntent
  ) {
    intentUrl = buildSrtMainIntentUrl(fallbackUrl);
  } else if (
    normalizedProvider === "SRT"
    && destination === "ticket"
    && deepLinkConfig.srtTicket.enabled
    && supportsAppIntent
  ) {
    intentUrl = buildSrtTicketIntentUrl(fallbackUrl);
  }
  return {
    url: intentUrl ?? fallbackUrl,
    usesAndroidApp: intentUrl !== null,
  };
}
