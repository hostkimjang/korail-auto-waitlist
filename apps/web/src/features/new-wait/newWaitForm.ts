import type { ProviderAccount, RailProvider } from "../../api/providerAccounts";
import type { ReservationPolicy } from "../../domain/reservationPolicy";
import { defaultReservationPolicy } from "./reservationPolicy";
import { halfHourBoundaryValue } from "./timeRange";

export const NEW_WAIT_WEEKDAY_LABELS = ["일", "월", "화", "수", "목", "금", "토"] as const;

export type NewWaitWeekday = (typeof NEW_WAIT_WEEKDAY_LABELS)[number];

export interface NewWaitForm {
  provider: RailProvider | "";
  providers: RailProvider[];
  origin: string;
  origin_node_id: string | null;
  destination: string;
  destination_node_id: string | null;
  date: string;
  time: string;
  timeEnd: string;
  selectedWeekdays: NewWaitWeekday[];
  passengers: string;
  seat: string;
  channels: string[];
  reservationPolicy: ReservationPolicy;
}

export interface CreateInitialNewWaitFormOptions {
  demo: boolean;
  providerAccounts: ReadonlyArray<ProviderAccount>;
  demoOriginNodeId: string | null;
  demoDestinationNodeId: string | null;
  now: Date;
}

export interface ToggleNewWaitProviderOptions {
  demo: boolean;
  providerAccounts: ReadonlyArray<ProviderAccount>;
  reservationPolicyManuallySelected: boolean;
}

function parseDateInput(value: string): Date {
  const [year = 0, month = 0, day = 0] = value.split("-").map(Number);
  return new Date(year, month - 1, day);
}

export function formatNewWaitDateLabel(value: string): string {
  return new Intl.DateTimeFormat("ko-KR", {
    month: "long",
    day: "numeric",
    weekday: "short",
  }).format(parseDateInput(value));
}

function dateInputValue(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function weekdayForDate(value: string): NewWaitWeekday {
  return NEW_WAIT_WEEKDAY_LABELS[parseDateInput(value).getDay()] ?? "일";
}

export function seoulDateInput(now: Date, dayOffset = 0): string {
  const value = new Date(now.getTime() + dayOffset * 24 * 60 * 60 * 1000);
  const parts = new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: "Asia/Seoul",
  }).formatToParts(value);
  const part = (type: Intl.DateTimeFormatPartTypes): string | undefined => (
    parts.find((item) => item.type === type)?.value
  );
  return `${part("year")}-${part("month")}-${part("day")}`;
}

interface NewWaitTravelWindow {
  date: string;
  time: string;
  timeEnd: string;
}

const HALF_HOUR_SECONDS = 30 * 60;
// 출발 시간 범위 slider와 같은 단위다. 48번째 경계는 서비스 날짜 종료 sentinel(23:59)이다.
const SERVICE_DATE_HALF_HOURS = 48;
// 예전 기본값(12:00–18:00)과 같은 6시간 폭을 유지해 기본 조회의 공식 시간표·좌석 조회량을 늘리지 않는다.
const DEFAULT_WINDOW_HALF_HOURS = 12;
// 오늘 남은 출발 범위가 1시간보다 짧으면(23시 이후) 빈 결과가 되기 쉬우므로 내일을 기본값으로 둔다.
const MIN_SAME_DAY_WINDOW_HALF_HOURS = 2;
const NEXT_DAY_DEFAULT_TIME = "12:00";
const NEXT_DAY_DEFAULT_TIME_END = "18:00";

function seoulSecondsOfDay(now: Date): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
    timeZone: "Asia/Seoul",
  }).formatToParts(now);
  const part = (type: Intl.DateTimeFormatPartTypes): number => (
    Number(parts.find((item) => item.type === type)?.value ?? 0)
  );
  return part("hour") * 60 * 60 + part("minute") * 60 + part("second");
}

/**
 * 새 대기를 여는 KST 시각에 맞춘 가는 날·출발 시간 범위 기본값이다. 이미 지난 출발 시각을 기본
 * 조회에 넣지 않도록 오늘은 아직 지나지 않은 첫 30분 경계부터 시작하고, 자정을 넘기지 않는다.
 */
function defaultNewWaitTravelWindow(now: Date): NewWaitTravelWindow {
  const startIndex = Math.ceil(seoulSecondsOfDay(now) / HALF_HOUR_SECONDS);
  if (startIndex + MIN_SAME_DAY_WINDOW_HALF_HOURS > SERVICE_DATE_HALF_HOURS) {
    return {
      date: seoulDateInput(now, 1),
      time: NEXT_DAY_DEFAULT_TIME,
      timeEnd: NEXT_DAY_DEFAULT_TIME_END,
    };
  }
  return {
    date: seoulDateInput(now),
    time: halfHourBoundaryValue(startIndex),
    timeEnd: halfHourBoundaryValue(
      Math.min(startIndex + DEFAULT_WINDOW_HALF_HOURS, SERVICE_DATE_HALF_HOURS),
    ),
  };
}

export function createInitialNewWaitForm({
  demo,
  providerAccounts,
  demoOriginNodeId,
  demoDestinationNodeId,
  now,
}: CreateInitialNewWaitFormOptions): NewWaitForm {
  const travelWindow = defaultNewWaitTravelWindow(now);
  return {
    provider: "KORAIL",
    providers: ["KORAIL"],
    origin: demo ? "서울" : "",
    origin_node_id: demo ? demoOriginNodeId : null,
    destination: demo ? "부산" : "",
    destination_node_id: demo ? demoDestinationNodeId : null,
    date: travelWindow.date,
    time: travelWindow.time,
    timeEnd: travelWindow.timeEnd,
    selectedWeekdays: [weekdayForDate(travelWindow.date)],
    passengers: "1",
    seat: "일반실",
    channels: ["web_push", "telegram"],
    reservationPolicy: demo
      ? "notify_only"
      : defaultReservationPolicy(["KORAIL"], providerAccounts),
  };
}

export function swapNewWaitStations(form: NewWaitForm): NewWaitForm {
  return {
    ...form,
    origin: form.destination,
    origin_node_id: form.destination_node_id,
    destination: form.origin,
    destination_node_id: form.origin_node_id,
  };
}

export function setNewWaitTravelDate(form: NewWaitForm, date: string): NewWaitForm {
  return {
    ...form,
    date,
    selectedWeekdays: [weekdayForDate(date)],
  };
}

export function nextWeekdayDate(
  baseValue: string,
  weekday: NewWaitWeekday,
  today: string,
): string {
  const base = parseDateInput(baseValue < today ? today : baseValue);
  const weekdayIndex = NEW_WAIT_WEEKDAY_LABELS.indexOf(weekday);
  const distance = (weekdayIndex - base.getDay() + 7) % 7;
  base.setDate(base.getDate() + distance);
  return dateInputValue(base);
}

export function selectNewWaitWeekday(
  form: NewWaitForm,
  weekday: NewWaitWeekday,
  today: string,
): NewWaitForm {
  return setNewWaitTravelDate(form, nextWeekdayDate(form.date, weekday, today));
}

export function toggleNewWaitProvider(
  form: NewWaitForm,
  provider: RailProvider,
  {
    demo,
    providerAccounts,
    reservationPolicyManuallySelected,
  }: ToggleNewWaitProviderOptions,
): NewWaitForm {
  const selected = form.providers.includes(provider)
    ? form.providers.filter((item) => item !== provider)
    : [...form.providers, provider];
  const defaultPolicy = demo
    ? "notify_only"
    : defaultReservationPolicy(selected, providerAccounts);
  const reservationPolicy = reservationPolicyManuallySelected
    && !(form.reservationPolicy === "reserve_once_before_payment" && defaultPolicy === "notify_only")
    ? form.reservationPolicy
    : defaultPolicy;

  return {
    ...form,
    providers: selected,
    provider: selected[0] ?? "",
    reservationPolicy,
  };
}
