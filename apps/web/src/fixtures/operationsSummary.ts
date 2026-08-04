import type { OperationsSummary } from "../api/operationsSummaryContract";

export const demoOperationsSummary: OperationsSummary = {
  generatedAt: "2026-07-29T12:30:00+09:00",
  window: { fromAt: "2026-07-28T12:30:00+09:00", toAt: "2026-07-29T12:30:00+09:00", hours: 24 },
  seatObservationErrorRate: {
    numerator: 2,
    denominator: 42,
    rate: 2 / 42,
    definition: "24시간 좌석 관측 오류율: 오류로 분류된 좌석 관측 / 같은 기간의 전체 좌석 관측",
  },
  notificationDeliveryFailureRate: {
    numerator: 1,
    denominator: 18,
    rate: 1 / 18,
    definition: "24시간 알림 전달 최종 실패율: 최종 실패 / (전달 성공 + 최종 실패). 대기 중인 알림은 제외",
  },
  windowCounts: {
    seatObservations: 42,
    seatObservationErrors: 2,
    reservationAttempts: 2,
    reservationFailures: 0,
    watchTransitions: 7,
    watchFailureTransitions: 1,
    notificationEvents: 19,
    notificationSent: 17,
    notificationFailed: 1,
  },
  currentCounts: {
    watchesByStatus: [{ status: "watching", count: 3 }, { status: "paused", count: 1 }],
    notificationOutboxPending: 1,
  },
  services: [
    { service: "api", status: "healthy", observedAt: "2026-07-29T12:30:00+09:00", evidence: "summary_request_succeeded" },
    { service: "database", status: "healthy", observedAt: "2026-07-29T12:30:00+09:00", evidence: "summary_query_succeeded" },
    { service: "worker", status: "unknown", observedAt: null, evidence: "durable_heartbeat_unavailable" },
    { service: "scheduler", status: "unknown", observedAt: null, evidence: "durable_heartbeat_unavailable" },
  ],
  sourceFreshness: [
    { source: "seat_observations", status: "fresh", observedAt: "2026-07-29T12:28:00+09:00", ageSeconds: 120, timestampBasis: "observed_at" },
    { source: "notification_delivery", status: "fresh", observedAt: "2026-07-29T12:29:00+09:00", ageSeconds: 60, timestampBasis: "processed_at" },
  ],
  providerCircuits: [
    { provider: "KORAIL", state: "closed", updatedAt: "2026-07-29T12:20:00+09:00", manualResumeRequired: false },
    { provider: "SRT", state: "manual_hold", updatedAt: "2026-07-29T12:21:00+09:00", manualResumeRequired: true },
  ],
  recentEntries: [
    { occurredAt: "2026-07-29T12:29:00+09:00", kind: "notification_delivery", level: "info", status: "sent", errorCategory: null, provider: null },
    { occurredAt: "2026-07-29T12:27:00+09:00", kind: "watch_transition", level: "info", status: "watching", errorCategory: null, provider: "KORAIL" },
  ],
  limitations: [
    "http_and_process_errors_are_not_durably_recorded",
    "worker_and_scheduler_health_require_durable_heartbeats",
    "recent_entries_are_sanitized_categories_without_identifiers_or_raw_errors",
  ],
  isPartial: true,
};
