/**
 * 배포된 화면이 어떤 개선까지 포함하는지 사용자가 직접 확인할 수 있도록 릴리스 버전과 날짜를
 * 한 곳에서 관리한다. 값을 바꿀 때는 저장소 루트 `CHANGELOG.md`의 맨 위 항목도 같은 작업에서
 * 갱신한다. 두 기록이 어긋나면 `tests/appRelease.test.ts`가 실패해 잘못된 버전이 배포되지 않는다.
 */
export interface AppRelease {
  /** 사용자에게 보여 주는 릴리스 번호. `주.부.수` 형식을 사용한다. */
  readonly version: string;
  /** 릴리스를 배포한 KST 기준 날짜. `YYYY-MM-DD` 형식이다. */
  readonly releasedOn: string;
}

export const APP_RELEASE: AppRelease = {
  version: "1.0.0",
  releasedOn: "2026-09-18",
};

export const CHANGELOG_URL =
  "https://github.com/hostkimjang/korail-auto-waitlist/blob/main/CHANGELOG.md";

const KOREAN_WEEKDAY_LABELS = ["일", "월", "화", "수", "목", "금", "토"] as const;
const ISO_DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;

/**
 * 달력에 실제로 존재하는 날짜만 통과시킨다. 표시용 계산이므로 UTC 자정으로 고정해
 * 실행 환경의 시간대가 요일을 바꾸지 않게 한다.
 */
function parseIsoDate(isoDate: string): Date | null {
  const match = ISO_DATE_PATTERN.exec(isoDate);
  if (match === null) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  const sameDate = parsed.getUTCFullYear() === year
    && parsed.getUTCMonth() === month - 1
    && parsed.getUTCDate() === day;
  return sameDate ? parsed : null;
}

/** 날짜 형식을 신뢰할 수 없으면 요일을 지어내지 않고 `null`을 돌려준다. */
export function koreanWeekdayLabel(isoDate: string): string | null {
  const parsed = parseIsoDate(isoDate);
  return parsed === null ? null : KOREAN_WEEKDAY_LABELS[parsed.getUTCDay()] ?? null;
}

/** `2026-09-18 (금)`. 요일을 계산할 수 없으면 날짜 원문만 보여 준다. */
export function formatReleaseDate(isoDate: string): string {
  const weekday = koreanWeekdayLabel(isoDate);
  return weekday === null ? isoDate : `${isoDate} (${weekday})`;
}

/** `v1.0.0 · 2026-09-18 (금)`. 로그나 진단 문구에서 한 줄로 쓰기 위한 표기다. */
export function formatReleaseLabel(release: AppRelease = APP_RELEASE): string {
  return `v${release.version} · ${formatReleaseDate(release.releasedOn)}`;
}
