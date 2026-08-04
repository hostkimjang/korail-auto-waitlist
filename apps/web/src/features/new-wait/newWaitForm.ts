import type { ProviderAccount, RailProvider } from "../../api/providerAccounts";
import type { ReservationPolicy } from "../../domain/reservationPolicy";
import { defaultReservationPolicy } from "./reservationPolicy";

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

export function createInitialNewWaitForm({
  demo,
  providerAccounts,
  demoOriginNodeId,
  demoDestinationNodeId,
  now,
}: CreateInitialNewWaitFormOptions): NewWaitForm {
  const initialDate = seoulDateInput(now, 1);
  return {
    provider: "KORAIL",
    providers: ["KORAIL"],
    origin: demo ? "서울" : "",
    origin_node_id: demo ? demoOriginNodeId : null,
    destination: demo ? "부산" : "",
    destination_node_id: demo ? demoDestinationNodeId : null,
    date: initialDate,
    time: "12:00",
    timeEnd: "18:00",
    selectedWeekdays: [weekdayForDate(initialDate)],
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
