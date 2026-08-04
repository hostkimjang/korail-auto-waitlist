import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchOperationsSummary } from "../src/api/operationsSummary";
import { mapOperationsSummary } from "../src/api/operationsSummaryContract";
import { fetchSeatStatusSources } from "../src/api/seatStatusSources";
import { mapSeatStatusSources } from "../src/api/seatStatusSourcesContract";

export function operationsPayload() {
  return {
    generated_at: "2026-07-29T03:30:00Z",
    window: { from_at: "2026-07-28T03:30:00Z", to_at: "2026-07-29T03:30:00Z", hours: 24 },
    seat_observation_error_rate: {
      numerator: 0,
      denominator: 0,
      rate: null,
      definition: "좌석 관측 오류 / 전체 좌석 관측",
    },
    notification_delivery_failure_rate: {
      numerator: 1,
      denominator: 10,
      rate: 0.1,
      definition: "최종 실패 / (전달 성공 + 최종 실패)",
    },
    window_counts: {
      seat_observations: 0,
      seat_observation_errors: 0,
      reservation_attempts: 3,
      reservation_failures: 1,
      watch_transitions: 8,
      watch_failure_transitions: 1,
      notification_events: 12,
      notification_sent: 9,
      notification_failed: 1,
    },
    current_counts: {
      watches_by_status: [{ status: "watching", count: 2 }],
      notification_outbox_pending: 2,
    },
    source_freshness: [{
      source: "notification_delivery",
      status: "fresh",
      observed_at: "2026-07-29T03:29:00Z",
      age_seconds: 60,
      timestamp_basis: "processed_at",
    }],
    services: [
      { service: "api", status: "healthy", observed_at: "2026-07-29T03:30:00Z", evidence: "summary_request_succeeded" },
      { service: "database", status: "healthy", observed_at: "2026-07-29T03:30:00Z", evidence: "summary_query_succeeded" },
    ],
    provider_circuits: [{
      provider: "korail",
      state: "closed",
      updated_at: "2026-07-29T03:25:00Z",
      manual_resume_required: false,
    }],
    recent_entries: [{
      occurred_at: "2026-07-29T03:29:00Z",
      kind: "notification_delivery",
      level: "info",
      status: "sent",
      error_category: null,
      provider: null,
    }],
    limitations: [
      "http_and_process_errors_are_not_durably_recorded",
      "worker_and_scheduler_health_require_durable_heartbeats",
      "recent_entries_are_sanitized_categories_without_identifiers_or_raw_errors",
    ],
  };
}

describe("operations summary API contract", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("maps the authenticated API shape and preserves explicit rate scope", () => {
    const result = mapOperationsSummary(operationsPayload());

    expect(result.window).toEqual({
      fromAt: "2026-07-28T03:30:00Z",
      toAt: "2026-07-29T03:30:00Z",
      hours: 24,
    });
    expect(result.seatObservationErrorRate).toMatchObject({
      numerator: 0,
      denominator: 0,
      rate: null,
      definition: "좌석 관측 오류 / 전체 좌석 관측",
    });
    expect(result.currentCounts.watchesByStatus).toEqual([{ status: "watching", count: 2 }]);
    expect(result.sourceFreshness[0]).toMatchObject({ source: "notification_delivery", status: "fresh" });
    expect(result.recentEntries[0]).toMatchObject({ kind: "notification_delivery", level: "info" });
  });

  it("fails closed for unknown enums and malformed timestamps", () => {
    const payload = operationsPayload();
    payload.services[0] = {
      service: "api",
      status: "surprising",
      observed_at: "not-a-date",
      evidence: "summary_request_succeeded",
    };
    payload.provider_circuits[0] = {
      provider: "korail",
      state: "bypassed",
      updated_at: "2026-07-29T03:25:00Z",
      manual_resume_required: false,
    };
    payload.recent_entries[0] = {
      occurred_at: "2026-07-29T03:29:00Z",
      kind: "notification_delivery",
      level: "debug",
      status: "sent",
      error_category: null,
      provider: null,
    };

    const result = mapOperationsSummary(payload);

    expect(result.services[0]).toMatchObject({ status: "unknown", observedAt: null });
    expect(result.providerCircuits[0]?.state).toBe("unknown");
    expect(result.recentEntries[0]?.level).toBe("warning");
    expect(result.isPartial).toBe(true);
  });

  it("uses the operations summary endpoint without sending payload data", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(operationsPayload()), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await fetchOperationsSummary();

    expect(fetchMock).toHaveBeenCalledWith("/api/v1/operations/summary", expect.objectContaining({
      credentials: "include",
      headers: { Accept: "application/json" },
    }));
    expect(fetchMock.mock.calls[0]?.[1]).not.toHaveProperty("body");
  });

  it("maps only the two recognised seat-status sources and closes malformed items", () => {
    const sources = mapSeatStatusSources([
      {
        provider: "korail",
        source: "korail_browser",
        state: "cooldown",
        cause: "provider_access_restricted",
        retry_after_seconds: 61.1,
      },
      {
        provider: "srt",
        source: "srt_live",
        state: "ready",
        cause: null,
        retry_after_seconds: null,
      },
      {
        provider: "korail",
        source: "unexpected_source",
        state: "ready",
        cause: null,
        retry_after_seconds: null,
      },
    ]);

    expect(sources).toEqual([
      {
        provider: "korail",
        source: "korail_browser",
        state: "cooldown",
        cause: "provider_access_restricted",
        retryAfterSeconds: 62,
      },
      {
        provider: "srt",
        source: "srt_live",
        state: "ready",
        cause: null,
        retryAfterSeconds: null,
      },
    ]);
    expect(mapSeatStatusSources({ sources: [] })).toEqual([
      { provider: "korail", source: "korail_browser", state: "unknown", cause: null, retryAfterSeconds: null },
      { provider: "srt", source: "srt_live", state: "unknown", cause: null, retryAfterSeconds: null },
    ]);
    expect(mapSeatStatusSources([
      {
        provider: "korail",
        source: "korail_browser",
        state: "cooldown",
        cause: "provider_access_restricted",
        retry_after_seconds: null,
      },
    ])[0]).toEqual({
      provider: "korail",
      source: "korail_browser",
      state: "unknown",
      cause: null,
      retryAfterSeconds: null,
    });
  });

  it("uses the authenticated seat-status source endpoint without sending payload data", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify([
      { provider: "korail", source: "korail_browser", state: "ready", cause: null, retry_after_seconds: null },
      { provider: "srt", source: "srt_live", state: "ready", cause: null, retry_after_seconds: null },
    ]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await fetchSeatStatusSources();

    expect(fetchMock).toHaveBeenCalledWith("/api/v1/seat-status/status", expect.objectContaining({
      credentials: "include",
      headers: { Accept: "application/json" },
    }));
    expect(fetchMock.mock.calls[0]?.[1]).not.toHaveProperty("body");
  });
});
