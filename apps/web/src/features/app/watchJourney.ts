export interface WatchJourneyContext {
  provider: string;
  train: string;
  seatClassLabel: string;
  date: string;
  route: string;
  departure: string;
  arrival: string;
}

export function formatWatchIdentity(context: WatchJourneyContext): string {
  return [context.provider, context.train, context.seatClassLabel]
    .filter((value) => value && !value.includes("정보 없음"))
    .join(" · ");
}

export function formatWatchSchedule(context: WatchJourneyContext): string {
  const timeRange = context.departure !== "--:--" && context.arrival !== "--:--"
    ? `${context.departure} → ${context.arrival}`
    : "";
  return [context.date, context.route, timeRange]
    .filter((value) => value && !value.includes("정보 없음") && value !== "날짜 미정")
    .join(" · ");
}
