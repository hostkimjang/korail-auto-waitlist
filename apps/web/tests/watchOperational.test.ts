import { afterEach, describe, expect, it, vi } from "vitest";

import { mapOperationalCandidate } from "../src/domain/watchOperational";

const healthyWatchContext = {
  provider: "KORAIL",
  watchStatus: "watching",
  nextCheckAt: "2026-08-02T13:32:01Z",
  observationExecutionState: "idle",
  cooldownUntil: null,
} as const;

const expiredOperationalCandidate = {
  operational_status: "scheduled",
  booking_window_status: "open",
  operational_source: "authorized-test-source",
  operational_observed_at: "2026-08-02T13:29:00Z",
  operational_fresh_until: "2026-08-02T13:29:01Z",
};

describe("mapOperationalCandidate", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows a current booking-window fact only while its provenance is fresh", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-02T13:30:00Z"));

    expect(mapOperationalCandidate({
      operational_status: "scheduled",
      booking_window_status: "open",
      operational_source: "authorized-test-source",
      operational_observed_at: "2026-08-02T13:29:00Z",
      operational_fresh_until: "2026-08-02T13:31:00Z",
    }, new Date("2026-08-02T13:30:00Z"), healthyWatchContext)).toMatchObject({
      fresh: true,
      label: "예매창 열림",
    });
  });

  it("reports a delayed observation without repeating expired current-tense details", () => {
    const mapped = mapOperationalCandidate({
      operational_status: "delayed",
      booking_window_status: "open",
      delay_minutes: 12,
      estimated_departure_at: "2026-08-02T13:45:00Z",
      operational_source: "authorized-test-source",
      operational_observed_at: "2026-08-02T13:29:00Z",
      operational_fresh_until: "2026-08-02T13:31:00Z",
    }, new Date("2026-08-02T13:32:00Z"), {
      ...healthyWatchContext,
      nextCheckAt: "2026-08-02T13:31:00Z",
    });

    expect(mapped).toMatchObject({
      fresh: false,
      label: "운행·예매 상태 관측 지연 · 응답 대기 중",
    });
    expect(mapped?.label).not.toContain("예매창 열림");
    expect(mapped?.label).not.toContain("12분 지연");
  });

  it.each([
    "sold_out",
    "unavailable",
    "not_enough_seats",
    "not_offered",
    "departed",
    "out_of_service",
  ])(
    "removes an expired non-terminal fact after a newer %s observation",
    (latestStatus) => {
      vi.useFakeTimers();
      vi.setSystemTime(new Date("2026-08-02T13:32:00Z"));

      const mapped = mapOperationalCandidate({
        operational_status: "scheduled",
        booking_window_status: "open",
        operational_source: "authorized-test-source",
        operational_observed_at: "2026-08-02T13:29:00Z",
        operational_fresh_until: "2026-08-02T13:31:00Z",
        latest_observation: {
          status: latestStatus,
          source: "authorized-test-source",
          observed_at: "2026-08-02T13:31:30Z",
          fresh_until: "2026-08-02T13:31:31Z",
          error_category: null,
        },
      }, new Date("2026-08-02T13:32:00Z"), healthyWatchContext);

      expect(mapped).toBeNull();
    },
  );

  it.each([
    "available",
    "limited",
    "standing_plus_seat",
    "waitlist_available",
    "reservation_completed",
    "sold_out",
    "unavailable",
    "not_enough_seats",
    "not_offered",
    "departed",
    "out_of_service",
  ])(
    "hides an expired non-terminal fact during a healthy %s observation cycle",
    (latestStatus) => {
      const mapped = mapOperationalCandidate({
        ...expiredOperationalCandidate,
        latest_observation: {
          status: latestStatus,
          source: "authorized-test-source",
          observed_at: "2026-08-02T13:29:00Z",
          fresh_until: "2026-08-02T13:29:01Z",
          error_category: null,
        },
      }, new Date("2026-08-02T13:32:00Z"), healthyWatchContext);

      expect(mapped).toBeNull();
    },
  );

  it("shows a canonical provider error even when the next retry is scheduled", () => {
    const mapped = mapOperationalCandidate({
      ...expiredOperationalCandidate,
      latest_observation: {
        status: "error",
        source: "authorized-test-source",
        observed_at: "2026-08-02T13:31:30Z",
        fresh_until: "2026-08-02T13:31:30Z",
        error_category: "provider_unavailable",
      },
    }, new Date("2026-08-02T13:32:00Z"), healthyWatchContext);

    expect(mapped?.label).toBe("운행·예매 상태 관측 오류 · 재시도 예정");
  });

  it("shows a canonical error before any operational projection exists", () => {
    const mapped = mapOperationalCandidate({
      operational_status: null,
      booking_window_status: null,
      latest_observation: {
        status: "error",
        source: "authorized-test-source",
        observed_at: "2026-08-02T13:31:30Z",
        fresh_until: "2026-08-02T13:31:30Z",
        error_category: "provider_unavailable",
      },
    }, new Date("2026-08-02T13:32:00Z"), healthyWatchContext);

    expect(mapped).toMatchObject({
      status: "unknown",
      observedAt: "2026-08-02T13:31:30Z",
      label: "운행·예매 상태 관측 오류 · 재시도 예정",
    });
  });

  it.each(["malformed", 1, []])(
    "fails closed when a present latest observation is malformed: %j",
    (latestObservation) => {
      const mapped = mapOperationalCandidate({
        latest_observation: latestObservation,
      }, new Date("2026-08-02T13:32:00Z"), healthyWatchContext);

      expect(mapped).toMatchObject({
        status: "unknown",
        label: "운행·예매 상태 확인 필요 · 재시도 예정",
      });
    },
  );

  it.each(["raw-provider-error", "", 7])(
    "does not treat an unrecognized error category as canonical: %j",
    (errorCategory) => {
      const mapped = mapOperationalCandidate({
        latest_observation: {
          status: "error",
          source: "authorized-test-source",
          observed_at: "2026-08-02T13:31:30Z",
          fresh_until: "2026-08-02T13:31:30Z",
          error_category: errorCategory,
        },
      }, new Date("2026-08-02T13:32:00Z"), healthyWatchContext);

      expect(mapped?.label).toBe("운행·예매 상태 확인 필요 · 재시도 예정");
    },
  );

  it.each([
    "scheduled",
    "watching",
    "official_waitlist",
    "seat_found",
    "cooldown",
  ] as const)("shows health warnings while %s monitoring is active", (watchStatus) => {
    const mapped = mapOperationalCandidate({
      latest_observation: {
        status: "error",
        source: "authorized-test-source",
        observed_at: "2026-08-02T13:31:30Z",
        fresh_until: "2026-08-02T13:31:30Z",
        error_category: "provider_unavailable",
      },
    }, new Date("2026-08-02T13:32:00Z"), {
      ...healthyWatchContext,
      watchStatus,
    });

    expect(mapped?.label).toBe("운행·예매 상태 관측 오류 · 재시도 예정");
  });

  it.each([
    "draft",
    "reserving",
    "payment_required",
    "completed",
    "paused",
    "auth_required",
    "expired",
    "failed",
  ] as const)("hides retained health warnings after entering %s", (watchStatus) => {
    const mapped = mapOperationalCandidate({
      latest_observation: {
        status: "error",
        source: "authorized-test-source",
        observed_at: "2026-08-02T13:31:30Z",
        fresh_until: "2026-08-02T13:31:30Z",
        error_category: "provider_unavailable",
      },
    }, new Date("2026-08-02T13:32:00Z"), {
      ...healthyWatchContext,
      watchStatus,
    });

    expect(mapped).toBeNull();
  });

  it("reports an overdue active observation before any operational projection exists", () => {
    const mapped = mapOperationalCandidate({
      operational_status: null,
      booking_window_status: null,
    }, new Date("2026-08-02T13:32:00Z"), {
      ...healthyWatchContext,
      nextCheckAt: "2026-08-02T13:31:29Z",
    });

    expect(mapped?.label).toBe("운행·예매 상태 관측 지연 · 응답 대기 중");
  });

  it("lets a newer canonical error replace a still-fresh non-terminal fact", () => {
    const mapped = mapOperationalCandidate({
      ...expiredOperationalCandidate,
      operational_fresh_until: "2026-08-02T13:35:00Z",
      latest_observation: {
        status: "error",
        source: "authorized-test-source",
        observed_at: "2026-08-02T13:31:30Z",
        fresh_until: "2026-08-02T13:31:30Z",
        error_category: "provider_unavailable",
      },
    }, new Date("2026-08-02T13:32:00Z"), healthyWatchContext);

    expect(mapped).toMatchObject({
      status: "unknown",
      bookingWindowStatus: "unknown",
      delayMinutes: null,
      label: "운행·예매 상태 관측 오류 · 재시도 예정",
    });
  });

  it.each([
    ["2026-08-02T13:28:59Z", "예매창 열림"],
    ["2026-08-02T13:29:00Z", "운행·예매 상태 관측 오류 · 재시도 예정"],
    ["2026-08-02T13:29:01Z", "운행·예매 상태 관측 오류 · 재시도 예정"],
  ])("orders an error at %s against the operational observation", (errorAt, expectedLabel) => {
    const mapped = mapOperationalCandidate({
      ...expiredOperationalCandidate,
      operational_fresh_until: "2026-08-02T13:35:00Z",
      latest_observation: {
        status: "error",
        source: "authorized-test-source",
        observed_at: errorAt,
        fresh_until: errorAt,
        error_category: "provider_unavailable",
      },
    }, new Date("2026-08-02T13:30:00Z"), healthyWatchContext);

    expect(mapped?.label).toBe(expectedLabel);
  });

  it.each([
    ["unknown", "운행·예매 상태 확인 필요 · 재시도 예정"],
    ["stale", "운행·예매 상태 관측 자료 만료 · 재시도 예정"],
  ])("keeps a newer %s observation visible as an uncertain retry", (status, label) => {
    const mapped = mapOperationalCandidate({
      ...expiredOperationalCandidate,
      latest_observation: {
        status,
        source: "authorized-test-source",
        observed_at: "2026-08-02T13:31:30Z",
        fresh_until: "2026-08-02T13:31:30Z",
        error_category: null,
      },
    }, new Date("2026-08-02T13:32:00Z"), healthyWatchContext);

    expect(mapped?.label).toBe(label);
  });

  it("hides an expired fact while a new observation request is in progress", () => {
    const mapped = mapOperationalCandidate(
      expiredOperationalCandidate,
      new Date("2026-08-02T13:32:00Z"),
      {
        ...healthyWatchContext,
        nextCheckAt: "2026-08-02T13:30:00Z",
        observationExecutionState: "in_progress",
      },
    );

    expect(mapped).toBeNull();
  });

  it("debounces a normal due target but reports a target overdue by more than 30 seconds", () => {
    expect(mapOperationalCandidate(
      expiredOperationalCandidate,
      new Date("2026-08-02T13:32:00Z"),
      { ...healthyWatchContext, nextCheckAt: "2026-08-02T13:31:30Z" },
    )).toBeNull();

    expect(mapOperationalCandidate(
      expiredOperationalCandidate,
      new Date("2026-08-02T13:32:00Z"),
      { ...healthyWatchContext, nextCheckAt: "2026-08-02T13:31:29Z" },
    )?.label).toBe("운행·예매 상태 관측 지연 · 응답 대기 중");
  });

  it("does not let a conclusive unavailable result hide a real scheduler delay", () => {
    const mapped = mapOperationalCandidate({
      ...expiredOperationalCandidate,
      latest_observation: {
        status: "sold_out",
        source: "authorized-test-source",
        observed_at: "2026-08-02T13:31:00Z",
        fresh_until: "2026-08-02T13:31:01Z",
        error_category: null,
      },
    }, new Date("2026-08-02T13:32:00Z"), {
      ...healthyWatchContext,
      nextCheckAt: "2026-08-02T13:31:00Z",
    });

    expect(mapped?.label).toBe("운행·예매 상태 관측 지연 · 응답 대기 중");
  });

  it("distinguishes an explicit provider cooldown from an observation delay", () => {
    const mapped = mapOperationalCandidate(
      expiredOperationalCandidate,
      new Date("2026-08-02T13:32:00Z"),
      {
        ...healthyWatchContext,
        nextCheckAt: "2026-08-02T13:40:00Z",
        cooldownUntil: "2026-08-02T13:40:00Z",
      },
    );

    expect(mapped?.label).toBe("운행·예매 상태 관측 일시 대기 · 22:40:00 재개 목표");
  });

  it("gives an active cooldown precedence over error, progress, and overdue signals", () => {
    const mapped = mapOperationalCandidate({
      ...expiredOperationalCandidate,
      latest_observation: {
        status: "error",
        source: "authorized-test-source",
        observed_at: "2026-08-02T13:31:30Z",
        fresh_until: "2026-08-02T13:31:30Z",
        error_category: "provider_unavailable",
      },
    }, new Date("2026-08-02T13:32:00Z"), {
      ...healthyWatchContext,
      nextCheckAt: "2026-08-02T13:30:00Z",
      observationExecutionState: "in_progress",
      cooldownUntil: "2026-08-02T13:40:00Z",
    });

    expect(mapped?.label).toBe(
      "운행·예매 상태 관측 일시 대기 · 22:40:00 재개 목표",
    );
  });

  it.each([
    ["2026-08-02T13:32:00Z", null],
    ["2026-08-02T13:31:59Z", null],
    ["malformed", null],
  ])("does not retain an expired cooldown at %s", (cooldownUntil, expected) => {
    const mapped = mapOperationalCandidate(
      expiredOperationalCandidate,
      new Date("2026-08-02T13:32:00Z"),
      {
        ...healthyWatchContext,
        cooldownUntil,
      },
    );

    expect(mapped).toBe(expected);
  });

  it("does not retain a provider cooldown message after monitoring is paused", () => {
    const mapped = mapOperationalCandidate(
      expiredOperationalCandidate,
      new Date("2026-08-02T13:32:00Z"),
      {
        ...healthyWatchContext,
        watchStatus: "paused",
        nextCheckAt: null,
        cooldownUntil: "2026-08-02T13:40:00Z",
      },
    );

    expect(mapped).toBeNull();
  });

  it("does not retain an observation error after monitoring is paused", () => {
    const mapped = mapOperationalCandidate({
      ...expiredOperationalCandidate,
      latest_observation: {
        status: "error",
        source: "authorized-test-source",
        observed_at: "2026-08-02T13:31:30Z",
        fresh_until: "2026-08-02T13:31:30Z",
        error_category: "provider_unavailable",
      },
    }, new Date("2026-08-02T13:32:00Z"), {
      ...healthyWatchContext,
      watchStatus: "paused",
      nextCheckAt: null,
    });

    expect(mapped).toBeNull();
  });

  it("keeps a terminal fact instead of replacing it with a newer health warning", () => {
    const mapped = mapOperationalCandidate({
      ...expiredOperationalCandidate,
      operational_status: "cancelled",
      booking_window_status: "closed",
      operational_fresh_until: "2026-08-02T13:35:00Z",
      latest_observation: {
        status: "error",
        source: "authorized-test-source",
        observed_at: "2026-08-02T13:31:30Z",
        fresh_until: "2026-08-02T13:31:30Z",
        error_category: "provider_unavailable",
      },
    }, new Date("2026-08-02T13:32:00Z"), healthyWatchContext);

    expect(mapped).toMatchObject({
      status: "cancelled",
      label: "운행 취소 · 예매 종료",
    });
  });

  it.each([
    [{ operational_status: "cancelled" }, "운행 취소"],
    [{ operational_status: "departed_origin" }, "출발역 통과"],
    [{ booking_window_status: "closed" }, "예매 종료"],
    [{ actual_departure_at: "2026-08-02T13:29:30Z" }, "실제 22:29"],
  ] as const)("preserves terminal facts across conflicting health signals", (terminal, label) => {
    const mapped = mapOperationalCandidate({
      ...expiredOperationalCandidate,
      operational_status: "unknown",
      booking_window_status: "unknown",
      ...terminal,
      latest_observation: {
        status: "error",
        source: "authorized-test-source",
        observed_at: "2026-08-02T13:31:30Z",
        fresh_until: "2026-08-02T13:31:30Z",
        error_category: "provider_unavailable",
      },
    }, new Date("2026-08-02T13:32:00Z"), {
      ...healthyWatchContext,
      nextCheckAt: "2026-08-02T13:30:00Z",
      observationExecutionState: "in_progress",
      cooldownUntil: "2026-08-02T13:40:00Z",
    });

    expect(mapped?.label).toBe(label);
  });

  it.each([
    ["KORAIL", "mock"],
    ["MOCK", "authorized-test-source"],
  ] as const)(
    "rejects a terminal fact whose source does not match the %s provider",
    (provider, operationalSource) => {
      const mapped = mapOperationalCandidate({
        ...expiredOperationalCandidate,
        operational_status: "cancelled",
        booking_window_status: "closed",
        operational_source: operationalSource,
      }, new Date("2026-08-02T13:32:00Z"), {
        ...healthyWatchContext,
        provider,
        nextCheckAt: "2026-08-02T13:32:01Z",
      });

      expect(mapped).toBeNull();
    },
  );

  it("does not report a monitoring delay for a watch that is no longer observed", () => {
    const mapped = mapOperationalCandidate(
      expiredOperationalCandidate,
      new Date("2026-08-02T13:32:00Z"),
      {
        ...healthyWatchContext,
        watchStatus: "payment_required",
        nextCheckAt: null,
      },
    );

    expect(mapped).toBeNull();
  });

  it.each([
    ["cancelled", "closed"],
    ["departed_origin", "closed"],
  ])(
    "does not erase a terminal %s fact after a newer sold-out observation",
    (operationalStatus, bookingWindowStatus) => {
      vi.useFakeTimers();
      vi.setSystemTime(new Date("2026-08-02T13:32:00Z"));

      const mapped = mapOperationalCandidate({
        operational_status: operationalStatus,
        booking_window_status: bookingWindowStatus,
        operational_source: "authorized-test-source",
        operational_observed_at: "2026-08-02T13:29:00Z",
        operational_fresh_until: "2026-08-02T13:31:00Z",
        latest_observation: {
          status: "sold_out",
          source: "authorized-test-source",
          observed_at: "2026-08-02T13:31:30Z",
          fresh_until: "2026-08-02T13:31:31Z",
          error_category: null,
        },
      }, new Date("2026-08-02T13:32:00Z"), healthyWatchContext);

      expect(mapped).toMatchObject({
        status: operationalStatus,
        bookingWindowStatus,
        fresh: false,
      });
      expect(mapped?.label).not.toContain("다시 확인 중");
    },
  );

  it("rejects a current operational fact without valid ordered provenance", () => {
    const candidate = {
      operational_status: "scheduled",
      booking_window_status: "open",
      operational_observed_at: "2026-08-02T13:29:00Z",
      operational_fresh_until: "2026-08-02T13:31:00Z",
    };

    expect(mapOperationalCandidate(
      candidate,
      new Date("2026-08-02T13:30:00Z"),
      healthyWatchContext,
    ))
      .toBeNull();
    expect(mapOperationalCandidate({
      ...candidate,
      operational_source: "invalid source",
    }, new Date("2026-08-02T13:30:00Z"), healthyWatchContext)).toBeNull();
    expect(mapOperationalCandidate({
      ...candidate,
      operational_source: "authorized-test-source",
      operational_fresh_until: "2026-08-02T13:28:59Z",
    }, new Date("2026-08-02T13:30:00Z"), healthyWatchContext)).toBeNull();
  });

  it("does not invent an operational message from unknown or malformed input", () => {
    expect(mapOperationalCandidate(
      null,
      new Date("2026-08-02T13:30:00Z"),
      healthyWatchContext,
    )).toBeNull();
    expect(mapOperationalCandidate({
      operational_status: "raw-provider-value",
      booking_window_status: "unknown",
    }, new Date("2026-08-02T13:30:00Z"), healthyWatchContext)).toBeNull();
  });
});
