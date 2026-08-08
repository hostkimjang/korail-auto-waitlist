export type NewWaitTimePreset = {
  label: "새벽" | "오전" | "오후" | "저녁";
  start: string;
  end: string;
};

// The timetable API accepts a single Korea service date and requires start < end.
// Keep the established 23:59 service-date sentinel internally while presenting
// that boundary to people as the following midnight.
export const SERVICE_DATE_END_TIME = "23:59";

export const NEW_WAIT_TIME_PRESETS: readonly NewWaitTimePreset[] = [
  { label: "새벽", start: "00:00", end: "09:00" },
  { label: "오전", start: "09:00", end: "12:00" },
  { label: "오후", start: "12:00", end: "18:00" },
  { label: "저녁", start: "18:00", end: SERVICE_DATE_END_TIME },
];

export function displayTimeBoundary(value: string): string {
  return value === SERVICE_DATE_END_TIME ? "00:00" : value;
}

export function accessibleTimeBoundary(value: string): string {
  return value === SERVICE_DATE_END_TIME ? "익일 00:00" : value;
}

export function displayTimeRange(start: string, end: string): string {
  return `${start}–${displayTimeBoundary(end)}`;
}

export function halfHourBoundaryIndex(value: string): number {
  if (value === SERVICE_DATE_END_TIME) return 48;
  return Number(value.slice(0, 2)) * 2 + Number(value.slice(3)) / 30;
}

export function halfHourBoundaryValue(index: number): string {
  if (index >= 48) return SERVICE_DATE_END_TIME;
  return `${String(Math.floor(index / 2)).padStart(2, "0")}:${index % 2 ? "30" : "00"}`;
}
