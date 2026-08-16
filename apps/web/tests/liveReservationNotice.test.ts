import { describe, expect, it } from "vitest";

import { buildLiveReservationNotice } from "../src/features/app/liveReservationNotice";
import type { WatchSnapshot } from "../src/features/app/watchSnapshots";
import type { WatchLifecycleSnapshot } from "../src/features/app/watchLifecycleSnapshot";

const watch: WatchLifecycleSnapshot = {
  id: "watch-korail-9248",
  status: "watching",
  provider: "KORAIL",
  route: "대전 → 서울",
  train: "9248",
  seatClassLabel: "일반실",
  date: "8월 4일 (화)",
  departure: "17:50",
  arrival: "18:58",
  latestReservationAttempt: null,
  paymentDeadline: null,
  reservationPolicy: "reserve_once_before_payment",
  reservationCandidateContexts: {
    candidate: {
      train: "9248",
      seatClassLabel: "일반실",
      date: "8월 4일 (화)",
      departure: "17:50",
      arrival: "18:58",
    },
  },
  seatFoundObservation: null,
  updatedAt: null,
};

describe("live reservation notices", () => {
  it("adapts and trims candidate context from the legacy public snapshot path", () => {
    const legacy: WatchSnapshot = {
      id: "legacy-live",
      status: "watching",
      provider: "KORAIL",
      route: "대전 → 서울",
      train: "KTX 038",
      seatClassLabel: "일반실",
      date: "8월 3일 (월)",
      departure: "14:35",
      arrival: "15:39",
      reservationCandidateContexts: {
        candidate: {
          train: "  KTX 240  ",
          seatClassLabel: "   ",
          date: "  8월 4일 (화)  ",
          departure: "  15:11  ",
          arrival: "   ",
        },
      },
    };

    const notice = buildLiveReservationNotice({
      id: "legacy-attempt",
      event_type: "watch.reservation_attempted",
      aggregate_id: legacy.id,
      created_at: "2026-08-03T12:09:45Z",
      payload: { watch_id: legacy.id, candidate_id: "candidate" },
    }, [legacy]);

    expect(notice).toMatchObject({
      meta: "KORAIL · KTX 240 · 일반실",
      description: "8월 4일 (화) · 대전 → 서울 · 15:11 → 15:39 · 세부 단계는 철도사 결과 수신 후 표시됩니다.",
    });
  });

  it("builds reserving directly from the attempted SSE without a REST reserving snapshot", () => {
    const notice = buildLiveReservationNotice({
      id: "attempt-event",
      event_type: "watch.reservation_attempted",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:09:45.851Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        outcome: "pending",
        seat_detected_at: "2026-08-03T12:09:44.500Z",
        attempt_started_at: "2026-08-03T12:09:45.000Z",
      },
    }, [watch]);

    expect(notice).toMatchObject({
      subjectKey: `watch:${watch.id}`,
      revisionKey: `watch:${watch.id}:attempt-event`,
      revisionAt: "2026-08-03T12:09:45.851Z",
      occurredAt: "2026-08-03T12:09:45.851Z",
      kind: "reserving",
      title: "예매를 진행하고 있습니다",
    });
    expect(notice?.steps?.map((step) => [step.label, step.state])).toEqual([
      ["좌석 발견", "completed"],
      ["자동 예매 요청 시작", "active"],
      ["철도사 응답·공식 결과 대기", "pending"],
    ]);
    expect(notice?.description).toContain("세부 단계는 철도사 결과 수신 후 표시");
  });

  it.each([
    ["missing candidate", { candidate_id: undefined }, watch.id],
    ["unknown candidate", { candidate_id: "unknown-candidate" }, watch.id],
    ["aggregate mismatch", { candidate_id: "candidate" }, "another-watch"],
  ])("rejects an SSE whose exact watch candidate identity is invalid: %s", (
    _label,
    payloadIdentity,
    aggregateId,
  ) => {
    const notice = buildLiveReservationNotice({
      id: `invalid-identity-${_label}`,
      event_type: "watch.reservation_result_requires_manual_check",
      aggregate_id: aggregateId,
      created_at: "2026-08-03T12:09:48Z",
      payload: {
        watch_id: watch.id,
        outcome: "unknown",
        manual_check_required: true,
        ...payloadIdentity,
      },
    }, [watch]);

    expect(notice).toBeNull();
  });

  it("does not mix a future REST observation or another attempt into an attempted SSE", () => {
    const notice = buildLiveReservationNotice({
      id: "attempt-with-newer-rest",
      event_type: "watch.reservation_attempted",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:09:45Z",
      payload: { watch_id: watch.id, candidate_id: "candidate", outcome: "pending" },
    }, [{
      ...watch,
      status: "seat_found",
      seatFoundObservation: { observedAt: "2026-08-03T12:09:48Z" },
      latestReservationAttempt: {
        startedAt: "2026-08-03T12:08:00Z",
        finishedAt: "2026-08-03T12:08:03Z",
        paymentHoldEndedAt: null,
      },
    }]);

    expect(notice).toMatchObject({
      startedAt: "2026-08-03T12:09:45Z",
      revisionAt: "2026-08-03T12:09:45Z",
    });
    expect(notice?.steps).toEqual([
      {
        label: "자동 예매 요청 시작",
        state: "active",
        occurredAt: "2026-08-03T12:09:45Z",
        durationPrefix: "감지 후",
      },
      { label: "철도사 응답·공식 결과 대기", state: "pending" },
    ]);
  });

  it("projects cumulative reservation progress without inventing future stages", () => {
    const notice = buildLiveReservationNotice({
      id: "progress-target-rechecked",
      event_type: "watch.reservation_progressed",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:09:47.950Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        attempt_id: "attempt-one",
        attempt_sequence: 1,
        seat_detected_at: "2026-08-03T12:09:44.500Z",
        attempt_started_at: "2026-08-03T12:09:45.000Z",
        stage: "target_rechecked",
        occurred_at: "2026-08-03T12:09:47.900Z",
        progress_stages: [
          { stage: "authenticated_session_ready", occurred_at: "2026-08-03T12:09:46.100Z" },
          { stage: "target_rechecked", occurred_at: "2026-08-03T12:09:47.900Z" },
        ],
      },
    }, [watch]);

    expect(notice).toMatchObject({
      subjectKey: `watch:${watch.id}`,
      revisionKey: `watch:${watch.id}:progress-target-rechecked`,
      kind: "reserving",
      durationMs: null,
    });
    expect(notice?.steps?.map((step) => [step.label, step.state])).toEqual([
      ["좌석 발견", "completed"],
      ["자동 예매 요청 시작", "completed"],
      ["로그인 세션 확인", "completed"],
      ["검색 결과·열차 재확인", "completed"],
      ["철도사 응답·공식 결과 대기", "active"],
    ]);
    expect(notice?.steps?.some((step) => step.label === "객실 등급 선택")).toBe(false);
  });

  it("accepts null seat detection without inventing its step or queue duration", () => {
    const notice = buildLiveReservationNotice({
      id: "progress-without-detection",
      event_type: "watch.reservation_progressed",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:09:46.200Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        attempt_id: "attempt-one",
        attempt_sequence: 1,
        seat_detected_at: null,
        attempt_started_at: "2026-08-03T12:09:45.000Z",
        stage: "authenticated_session_ready",
        occurred_at: "2026-08-03T12:09:46.100Z",
        progress_stages: [
          { stage: "authenticated_session_ready", occurred_at: "2026-08-03T12:09:46.100Z" },
        ],
      },
    }, [watch]);

    expect(notice?.steps?.map((step) => step.label)).toEqual([
      "자동 예매 요청 시작",
      "로그인 세션 확인",
      "철도사 응답·공식 결과 대기",
    ]);
    expect(notice?.steps?.[0]).not.toHaveProperty("durationMs");
  });

  it.each([
    ["missing attempt identity", { attempt_id: null }],
    ["invalid attempt sequence", { attempt_sequence: 0 }],
    ["mismatched current stage", { stage: "seat_selected" }],
    ["future current time", { occurred_at: "2026-08-03T12:09:48.500Z" }],
  ])("rejects malformed cumulative progress: %s", (_label, overrides) => {
    const payload = {
      watch_id: watch.id,
      candidate_id: "candidate",
      attempt_id: "attempt-one",
      attempt_sequence: 1,
      seat_detected_at: null,
      attempt_started_at: "2026-08-03T12:09:45.000Z",
      stage: "target_rechecked",
      occurred_at: "2026-08-03T12:09:47.900Z",
      progress_stages: [
        { stage: "authenticated_session_ready", occurred_at: "2026-08-03T12:09:46.100Z" },
        { stage: "target_rechecked", occurred_at: "2026-08-03T12:09:47.900Z" },
      ],
      ...overrides,
    };
    expect(buildLiveReservationNotice({
      id: "invalid-progress",
      event_type: "watch.reservation_progressed",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:09:47.950Z",
      payload,
    }, [watch])).toBeNull();
  });

  it("replaces progress with a timestamped not-available recovery result", () => {
    const notice = buildLiveReservationNotice({
      id: "result-event",
      event_type: "watch.reservation_result",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:09:48.367Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        seat_detected_at: "2026-08-03T12:09:44.500Z",
        attempt_started_at: "2026-08-03T12:09:45.851Z",
        attempt_finished_at: "2026-08-03T12:09:48.250Z",
        outcome: "not_available",
        retryable: true,
        manual_check_required: false,
        retry_condition: "new_availability_episode",
        progress_stages: [
          {
            stage: "authenticated_session_ready",
            occurred_at: "2026-08-03T12:09:46.100Z",
          },
          { stage: "target_rechecked", occurred_at: "2026-08-03T12:09:47.900Z" },
        ],
      },
    }, [watch]);

    expect(notice).toMatchObject({
      subjectKey: `watch:${watch.id}`,
      revisionKey: `watch:${watch.id}:result-event`,
      occurredAt: "2026-08-03T12:09:48.367Z",
      kind: "recovery",
      title: "좌석이 사라져 다시 감시 중입니다",
    });
    expect(notice?.steps).toEqual([
      {
        label: "좌석 발견",
        state: "completed",
        occurredAt: "2026-08-03T12:09:44.500Z",
      },
      {
        label: "자동 예매 요청 시작",
        state: "completed",
        occurredAt: "2026-08-03T12:09:45.851Z",
        durationMs: 1_351,
        durationPrefix: "감지 후",
      },
      {
        label: "로그인 세션 확인",
        state: "completed",
        occurredAt: "2026-08-03T12:09:46.100Z",
        durationMs: 249,
        durationPrefix: "이전 단계 후",
      },
      {
        label: "검색 결과·열차 재확인",
        state: "failed",
        occurredAt: "2026-08-03T12:09:47.900Z",
        durationMs: 1_800,
        durationPrefix: "이전 단계 후",
      },
      {
        label: "공식 결과 확인",
        state: "completed",
        occurredAt: "2026-08-03T12:09:48.250Z",
        durationMs: 350,
        durationPrefix: "이전 단계 후",
      },
      {
        label: "감시·재예매 대기",
        state: "active",
        occurredAt: "2026-08-03T12:09:48.367Z",
      },
    ]);
  });

  it("preserves every measured stage from the observed 7.717 second result payload", () => {
    const notice = buildLiveReservationNotice({
      id: "result-observed-246",
      event_type: "watch.reservation_result",
      aggregate_id: watch.id,
      created_at: "2026-08-10T13:01:21.646Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        attempt_id: "attempt-246",
        attempt_sequence: 246,
        seat_detected_at: null,
        attempt_started_at: "2026-08-10T13:01:13.901Z",
        attempt_finished_at: "2026-08-10T13:01:21.618Z",
        outcome: "payment_required",
        reserved_seats: [{ car_number: "2", seat_number: "7C" }],
        progress_stages: [
          { stage: "authenticated_session_ready", occurred_at: "2026-08-10T13:01:18.506Z" },
          { stage: "target_rechecked", occurred_at: "2026-08-10T13:01:19.651Z" },
          { stage: "seat_selected", occurred_at: "2026-08-10T13:01:19.775Z" },
          { stage: "reservation_requested", occurred_at: "2026-08-10T13:01:19.799Z" },
        ],
      },
    }, [watch]);

    expect(notice?.durationMs).toBe(7_717);
    expect(notice?.meta).toContain("예약 좌석 2호차 7C");
    expect(notice?.steps?.map((step) => [step.label, step.durationMs])).toEqual([
      ["좌석 발견", undefined],
      ["자동 예매 요청 시작", undefined],
      ["로그인 세션 확인", 4_605],
      ["검색 결과·열차 재확인", 1_145],
      ["객실 등급 선택", 124],
      ["예약 요청", 24],
      ["공식 결과 확인", 1_819],
      ["공식 결제 필요", undefined],
    ]);
  });

  it("drops unknown or duplicate provider progress instead of inventing steps", () => {
    const notice = buildLiveReservationNotice({
      id: "result-with-invalid-progress",
      event_type: "watch.reservation_result",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:09:48.367Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        outcome: "not_available",
        retryable: true,
        manual_check_required: false,
        retry_condition: "new_availability_episode",
        progress_stages: [
          { stage: "internal_dom_probe", occurred_at: "2026-08-03T12:09:46Z" },
          { stage: "target_rechecked", occurred_at: "not-a-time" },
        ],
      },
    }, [watch]);

    expect(notice?.steps?.some((step) => step.label === "로그인 세션 확인")).toBe(false);
  });

  it("turns the dedicated manual-check SSE into a sticky result notice", () => {
    const notice = buildLiveReservationNotice({
      id: "manual-event",
      event_type: "watch.reservation_result_requires_manual_check",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:10:01Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        monitoring_resumed: false,
      },
    }, [watch]);

    expect(notice).toMatchObject({
      kind: "manual_check",
      title: "예매 결과를 확인해야 합니다",
      autoCloseMs: null,
    });
    expect(notice?.description).toContain("감시는 종료되었습니다");
    expect(notice?.steps?.at(-1)?.label).toBe("공식 결과 수동 확인");
  });

  it("separates a provider action reason and exposes bounded official rechecks", () => {
    const notice = buildLiveReservationNotice({
      id: "provider-action-result",
      event_type: "watch.reservation_result",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:10:01Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        outcome: "unknown",
        retryable: false,
        manual_check_required: true,
        result_reason_code: "delay_consent_required",
        confirmation_outcome: "inconclusive",
        confirmation_diagnostic_code: "official_evidence_insufficient",
        confirmation_observed_at: "2026-08-03T12:10:00Z",
        reconciliation_attempt_count: 3,
        next_reconcile_at: "2026-08-03T12:12:00Z",
        provider_error: "raw-provider-dialog-must-not-render",
      },
    }, [watch]);

    expect(notice).toMatchObject({
      kind: "manual_check",
      title: "운행 지연 동의가 필요합니다",
    });
    expect(notice?.description).toContain("철도사 지연 안내창에서 운행 지연 동의가 필요합니다");
    expect(notice?.description).toContain(
      "공식 내역은 확인했지만 예약 상태를 확정할 정보가 충분하지 않습니다.",
    );
    expect(notice?.description).toContain("공식 내역 자동 재확인 3/6회 수행");
    expect(notice?.description).not.toContain("raw-provider-dialog");
  });

  it("normalizes legacy and future SSE diagnostics and ignores them for conclusive evidence", () => {
    const event = {
      event_type: "watch.reservation_result",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:10:01Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        outcome: "unknown",
        retryable: false,
        manual_check_required: true,
        result_reason_code: "reservation_request_result_unknown",
        confirmation_observed_at: "2026-08-03T12:10:00Z",
        reconciliation_attempt_count: 1,
        next_reconcile_at: null,
      },
    };
    const legacy = buildLiveReservationNotice({
      ...event,
      id: "legacy-inconclusive-diagnostic",
      payload: { ...event.payload, confirmation_outcome: "inconclusive" },
    }, [watch]);
    const future = buildLiveReservationNotice({
      ...event,
      id: "future-inconclusive-diagnostic",
      payload: {
        ...event.payload,
        confirmation_outcome: "inconclusive",
        confirmation_diagnostic_code: "future_diagnostic",
      },
    }, [watch]);
    const conclusive = buildLiveReservationNotice({
      ...event,
      id: "conclusive-with-diagnostic",
      payload: {
        ...event.payload,
        confirmation_outcome: "not_found",
        confirmation_diagnostic_code: "official_read_unavailable",
      },
    }, [watch]);

    for (const notice of [legacy, future]) {
      expect(notice?.description).toContain(
        "공식 예약 내역 확인으로 결과를 확정하지 못했습니다.",
      );
    }
    expect(conclusive?.description).toContain("공식 예약 내역에서 대상 예약을 찾지 못했습니다.");
    expect(conclusive?.description).not.toContain(
      "철도사 공식 내역을 불러오거나 응답을 확인하지 못했습니다.",
    );
  });

  it("updates the same unknown attempt from a reconciled SSE revision", () => {
    const pendingConfirmation: WatchLifecycleSnapshot = {
      ...watch,
      latestReservationAttemptCandidateId: "candidate",
      latestReservationAttempt: {
        outcome: "unknown",
        resultReasonCode: "reservation_request_result_unknown",
        startedAt: "2026-08-03T12:00:00Z",
        finishedAt: "2026-08-03T12:00:01Z",
        retryable: false,
        manualCheckRequired: true,
        retryCondition: null,
        progressStages: [
          { stage: "authenticated_session_ready", occurredAt: "2026-08-03T12:00:00.500Z" },
        ],
        reservedSeats: [{ carNumber: "1", seatNumber: "1A" }],
        paymentHoldEndedAt: null,
        confirmationOutcome: "inconclusive",
        confirmationObservedAt: "2026-08-03T12:10:00Z",
        reconciliationAttemptCount: 5,
        nextReconcileAt: "2026-08-03T12:12:00Z",
      },
    };
    const notice = buildLiveReservationNotice({
      id: "reconciled-six",
      event_type: "watch.reservation_reconciled",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:15:01Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        outcome: "unknown",
        attempt_started_at: "2026-08-03T12:09:45Z",
        attempt_finished_at: "2026-08-03T12:09:48Z",
        progress_stages: [
          { stage: "authenticated_session_ready", occurred_at: "2026-08-03T12:09:46Z" },
          { stage: "target_rechecked", occurred_at: "2026-08-03T12:09:47Z" },
          { stage: "seat_selected", occurred_at: "2026-08-03T12:09:47.500Z" },
          { stage: "reservation_requested", occurred_at: "2026-08-03T12:09:47.750Z" },
        ],
        reserved_seats: [{ car_number: "2", seat_number: "7C" }],
        result_reason_code: "reservation_request_result_unknown",
        confirmation_outcome: "inconclusive",
        confirmation_observed_at: "2026-08-03T12:15:00Z",
        reconciliation_attempt_count: 6,
        next_reconcile_at: null,
        retryable: false,
      },
    }, [pendingConfirmation]);

    expect(notice).toMatchObject({
      revisionKey: `watch:${watch.id}:reconciled-six`,
      revisionAt: "2026-08-03T12:15:01Z",
      kind: "manual_check",
      title: "예매 요청 결과를 확인해야 합니다",
    });
    expect(notice?.description).toContain("공식 내역 자동 재확인 6/6회 수행");
    expect(notice?.description).toContain("공식 예약 내역 확인으로 결과를 확정하지 못했습니다");
    expect(notice?.description).not.toMatch(/결제 (미완료|완료)/);
    expect(notice).toMatchObject({
      startedAt: "2026-08-03T12:09:45Z",
      durationMs: 3_000,
      meta: expect.stringContaining("예약 좌석 2호차 7C"),
    });
    expect(notice?.meta).not.toContain("1호차 1A");
    expect(notice?.steps?.find((step) => step.label === "검색 결과·열차 재확인"))
      .toMatchObject({ durationMs: 1_000, state: "completed" });

    const legacyWithoutAttemptContext = buildLiveReservationNotice({
      id: "reconciled-six-legacy-context",
      event_type: "watch.reservation_reconciled",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:15:02Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        outcome: "unknown",
        result_reason_code: "reservation_request_result_unknown",
        confirmation_outcome: "inconclusive",
        confirmation_observed_at: "2026-08-03T12:15:00Z",
        reconciliation_attempt_count: 6,
        next_reconcile_at: null,
        retryable: false,
      },
    }, [pendingConfirmation]);

    expect(legacyWithoutAttemptContext).toMatchObject({
      kind: "manual_check",
      startedAt: null,
      durationMs: null,
    });
    expect(legacyWithoutAttemptContext?.meta).not.toContain("예약 좌석");
  });

  it("waits for the dedicated terminal event instead of projecting confirmed-paid reconciliation", () => {
    const paymentWatch: WatchLifecycleSnapshot = {
      ...watch,
      status: "payment_required",
      paymentDeadline: "2026-08-03T13:00:00Z",
      latestReservationAttemptCandidateId: "candidate",
      latestReservationAttempt: {
        outcome: "payment_required",
        resultReasonCode: "payment_hold_created",
        startedAt: "2026-08-03T12:00:00Z",
        finishedAt: "2026-08-03T12:00:01Z",
        retryable: false,
        manualCheckRequired: false,
        retryCondition: null,
        progressStages: [
          { stage: "authenticated_session_ready", occurredAt: "2026-08-03T12:00:00.500Z" },
        ],
        reservedSeats: [{ carNumber: "1", seatNumber: "1A" }],
        paymentHoldEndedAt: null,
      },
    };

    expect(buildLiveReservationNotice({
      id: "reconciled-paid-before-terminal",
      event_type: "watch.reservation_reconciled",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:15:01Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        outcome: "payment_required",
        payment_actionable: true,
        attempt_started_at: "2026-08-03T12:09:45Z",
        attempt_finished_at: "2026-08-03T12:09:48Z",
        progress_stages: [
          { stage: "authenticated_session_ready", occurred_at: "2026-08-03T12:09:46Z" },
          { stage: "target_rechecked", occurred_at: "2026-08-03T12:09:47Z" },
          { stage: "seat_selected", occurred_at: "2026-08-03T12:09:47.500Z" },
          { stage: "reservation_requested", occurred_at: "2026-08-03T12:09:47.750Z" },
        ],
        reserved_seats: [{ car_number: "2", seat_number: "7C" }],
        payment_deadline: "2026-08-03T12:30:00Z",
        result_reason_code: "payment_hold_created",
        confirmation_outcome: "confirmed_paid",
        confirmation_observed_at: "2026-08-03T12:15:00Z",
        reconciliation_attempt_count: 3,
        next_reconcile_at: null,
      },
    }, [paymentWatch])).toBeNull();
  });

  it.each([
    ["auth_required", "로그인 확인이 필요합니다", "공식 예약 내역을 확인하려면 로그인이 필요합니다"],
    ["provider_blocked", "운영사 요청 제한으로 확인이 필요합니다", "운영사 제한으로 공식 예약 내역을 확인하지 못했습니다"],
  ] as const)("surfaces %s confirmation on an unknown attempt as an authentication action", (
    confirmationOutcome,
    title,
    evidence,
  ) => {
    const unknownWatch: WatchLifecycleSnapshot = {
      ...watch,
      latestReservationAttemptCandidateId: "candidate",
      latestReservationAttempt: {
        outcome: "unknown",
        resultReasonCode: "reservation_request_result_unknown",
        startedAt: "2026-08-03T12:09:45Z",
        finishedAt: "2026-08-03T12:09:48Z",
        retryable: false,
        manualCheckRequired: true,
        retryCondition: null,
        paymentHoldEndedAt: null,
      },
    };
    const notice = buildLiveReservationNotice({
      id: `reconciled-${confirmationOutcome}-unknown`,
      event_type: "watch.reservation_reconciled",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:15:01Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        outcome: "unknown",
        result_reason_code: "reservation_request_result_unknown",
        confirmation_outcome: confirmationOutcome,
        confirmation_observed_at: "2026-08-03T12:15:00Z",
        reconciliation_attempt_count: 2,
        next_reconcile_at: null,
      },
    }, [unknownWatch]);

    expect(notice).toMatchObject({
      kind: "auth_required",
      title,
      autoCloseMs: null,
    });
    expect(notice?.description).toContain(evidence);
  });

  it("rejects stale UNKNOWN authentication reconciliation that does not own the latest attempt", () => {
    const newerAttemptWatch: WatchLifecycleSnapshot = {
      ...watch,
      latestReservationAttemptCandidateId: "newer-candidate",
      reservationCandidateContexts: {
        ...watch.reservationCandidateContexts,
        "newer-candidate": {
          train: "KTX 253",
          seatClassLabel: "일반실",
          date: "8월 4일 (화)",
          departure: "18:10",
          arrival: "19:18",
        },
      },
      latestReservationAttempt: {
        outcome: "unknown",
        resultReasonCode: "reservation_request_result_unknown",
        startedAt: "2026-08-03T12:14:00Z",
        finishedAt: "2026-08-03T12:14:03Z",
        retryable: false,
        manualCheckRequired: true,
        retryCondition: null,
        paymentHoldEndedAt: null,
      },
    };

    expect(buildLiveReservationNotice({
      id: "stale-reconciled-auth-unknown",
      event_type: "watch.reservation_reconciled",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:15:01Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        outcome: "unknown",
        result_reason_code: "reservation_request_result_unknown",
        confirmation_outcome: "auth_required",
        confirmation_observed_at: "2026-08-03T12:15:00Z",
        reconciliation_attempt_count: 2,
        next_reconcile_at: null,
      },
    }, [newerAttemptWatch])).toBeNull();
  });

  it("keeps provider-blocked confirmation on a payment hold as payment guidance", () => {
    const paymentWatch: WatchLifecycleSnapshot = {
      ...watch,
      status: "payment_required",
      latestReservationAttemptCandidateId: "candidate",
      latestReservationAttempt: {
        outcome: "payment_required",
        resultReasonCode: "payment_hold_created",
        startedAt: "2026-08-03T12:09:45Z",
        finishedAt: "2026-08-03T12:09:48Z",
        retryable: false,
        manualCheckRequired: false,
        retryCondition: null,
        paymentHoldEndedAt: null,
      },
    };
    const notice = buildLiveReservationNotice({
      id: "reconciled-blocked-payment",
      event_type: "watch.reservation_reconciled",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:15:01Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        outcome: "payment_required",
        payment_actionable: true,
        result_reason_code: "payment_hold_created",
        confirmation_outcome: "provider_blocked",
        confirmation_observed_at: "2026-08-03T12:15:00Z",
        reconciliation_attempt_count: 2,
        next_reconcile_at: "2026-08-03T12:17:00Z",
      },
    }, [paymentWatch]);

    expect(notice).toMatchObject({
      kind: "payment_required",
      title: "결제 직전까지 예매되었습니다",
    });
    expect(notice?.description).toContain("운영사 제한으로 공식 예약 내역을 확인하지 못했습니다");
    expect(notice?.title).not.toBe("운영사 요청 제한으로 확인이 필요합니다");
  });

  it("keeps an actionable payment hold while not-found or inconclusive evidence is refreshed", () => {
    const paymentWatch: WatchLifecycleSnapshot = {
      ...watch,
      status: "payment_required",
      paymentDeadline: "2026-08-03T13:00:00Z",
      latestReservationAttemptCandidateId: "candidate",
      latestReservationAttempt: {
        outcome: "payment_required",
        resultReasonCode: "payment_hold_created",
        startedAt: "2026-08-03T12:00:00Z",
        finishedAt: "2026-08-03T12:00:01Z",
        retryable: false,
        manualCheckRequired: false,
        retryCondition: null,
        progressStages: [
          { stage: "authenticated_session_ready", occurredAt: "2026-08-03T12:00:00.500Z" },
        ],
        reservedSeats: [{ carNumber: "1", seatNumber: "1A" }],
        paymentHoldEndedAt: null,
      },
    };
    const event = {
      event_type: "watch.reservation_reconciled",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:15:01Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        outcome: "payment_required",
        payment_actionable: true,
        attempt_started_at: "2026-08-03T12:09:45Z",
        attempt_finished_at: "2026-08-03T12:09:48Z",
        progress_stages: [
          { stage: "authenticated_session_ready", occurred_at: "2026-08-03T12:09:46Z" },
          { stage: "target_rechecked", occurred_at: "2026-08-03T12:09:47Z" },
          { stage: "seat_selected", occurred_at: "2026-08-03T12:09:47.500Z" },
          { stage: "reservation_requested", occurred_at: "2026-08-03T12:09:47.750Z" },
        ],
        reserved_seats: [{ car_number: "2", seat_number: "7C" }],
        payment_deadline: "2026-08-03T12:30:00Z",
        result_reason_code: "payment_hold_created",
        confirmation_observed_at: "2026-08-03T12:15:00Z",
        reconciliation_attempt_count: 2,
        next_reconcile_at: "2026-08-03T12:17:00Z",
      },
    };
    const notFound = buildLiveReservationNotice({
      ...event,
      id: "reconciled-payment-not-found",
      payload: { ...event.payload, confirmation_outcome: "not_found" },
    }, [paymentWatch]);
    const inconclusive = buildLiveReservationNotice({
      ...event,
      id: "reconciled-payment-inconclusive",
      payload: { ...event.payload, confirmation_outcome: "inconclusive" },
    }, [paymentWatch]);

    expect(notFound).toMatchObject({
      kind: "payment_required",
      title: "결제 직전까지 예매되었습니다",
      sortAt: "2026-08-03T12:30:00Z",
      startedAt: "2026-08-03T12:09:45Z",
      durationMs: 3_000,
      meta: expect.stringContaining("예약 좌석 2호차 7C"),
    });
    expect(notFound?.meta).not.toContain("1호차 1A");
    expect(notFound?.description).toContain("공식 예약 내역에서 대상 예약을 찾지 못했습니다");
    expect(inconclusive).toMatchObject({
      kind: "payment_required",
      title: "결제 직전까지 예매되었습니다",
      sortAt: "2026-08-03T12:30:00Z",
      startedAt: "2026-08-03T12:09:45Z",
      durationMs: 3_000,
      meta: expect.stringContaining("예약 좌석 2호차 7C"),
    });
    expect(inconclusive?.description).toContain("공식 예약 내역 확인으로 결과를 확정하지 못했습니다");
  });

  it("does not infer payment context from a different latest candidate", () => {
    const paymentWatch: WatchLifecycleSnapshot = {
      ...watch,
      status: "payment_required",
      paymentDeadline: "2026-08-03T13:00:00Z",
      reservationCandidateContexts: {
        ...watch.reservationCandidateContexts,
        "other-candidate": {
          train: "1001",
          seatClassLabel: "특실",
          date: "8월 4일 (화)",
          departure: "18:10",
          arrival: "19:20",
        },
      },
      latestReservationAttemptCandidateId: "candidate",
      latestReservationAttempt: {
        outcome: "payment_required",
        resultReasonCode: "payment_hold_created",
        startedAt: "2026-08-03T12:09:45Z",
        finishedAt: "2026-08-03T12:09:48Z",
        retryable: false,
        manualCheckRequired: false,
        retryCondition: null,
        progressStages: [
          { stage: "authenticated_session_ready", occurredAt: "2026-08-03T12:09:46Z" },
        ],
        reservedSeats: [{ carNumber: "1", seatNumber: "1A" }],
        paymentHoldEndedAt: null,
      },
    };
    const notice = buildLiveReservationNotice({
      id: "reconciled-other-candidate-without-context",
      event_type: "watch.reservation_reconciled",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:15:01Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "other-candidate",
        outcome: "payment_required",
        payment_actionable: true,
        result_reason_code: "payment_hold_created",
        confirmation_outcome: "inconclusive",
        confirmation_observed_at: "2026-08-03T12:15:00Z",
        reconciliation_attempt_count: 2,
        next_reconcile_at: "2026-08-03T12:17:00Z",
      },
    }, [paymentWatch]);

    expect(notice).toMatchObject({
      kind: "payment_required",
      sortAt: null,
      startedAt: null,
      durationMs: null,
      meta: "KORAIL · 1001 · 특실",
    });
    expect(notice?.meta).not.toContain("1호차 1A");
  });

  it("requires canonical outcome and explicit actionability before showing payment-required", () => {
    const paymentWatch: WatchLifecycleSnapshot = {
      ...watch,
      status: "payment_required",
      latestReservationAttemptCandidateId: "candidate",
      latestReservationAttempt: {
        outcome: "payment_required",
        resultReasonCode: "payment_hold_created",
        startedAt: "2026-08-03T12:09:45Z",
        finishedAt: "2026-08-03T12:09:48Z",
        retryable: false,
        manualCheckRequired: false,
        retryCondition: null,
        paymentHoldEndedAt: null,
      },
    };
    const event = {
      id: "reconciled-payment-required",
      event_type: "watch.reservation_reconciled",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:15:01Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        result_reason_code: "payment_hold_created",
        confirmation_outcome: "confirmed_payment_required",
        confirmation_observed_at: "2026-08-03T12:15:00Z",
        reconciliation_attempt_count: 2,
        next_reconcile_at: "2026-08-03T12:17:00Z",
      },
    };
    const promoted = buildLiveReservationNotice({
      ...event,
      payload: {
        ...event.payload,
        outcome: "payment_required",
        payment_actionable: true,
      },
    }, [paymentWatch]);
    const legacyWithoutOutcome = buildLiveReservationNotice({
      ...event,
      payload: { ...event.payload, payment_actionable: true },
    }, [paymentWatch]);
    const legacyWithoutActionability = buildLiveReservationNotice({
      ...event,
      payload: { ...event.payload, outcome: "payment_required" },
    }, [paymentWatch]);
    const nonActionable = buildLiveReservationNotice({
      ...event,
      payload: {
        ...event.payload,
        outcome: "payment_required",
        payment_actionable: false,
      },
    }, [paymentWatch]);
    const malformedOutcome = buildLiveReservationNotice({
      ...event,
      payload: {
        ...event.payload,
        outcome: "PAYMENT_REQUIRED",
        payment_actionable: true,
      },
    }, [paymentWatch]);
    const malformedActionability = buildLiveReservationNotice({
      ...event,
      payload: {
        ...event.payload,
        outcome: "payment_required",
        payment_actionable: "true",
      },
    }, [paymentWatch]);

    expect(promoted).toMatchObject({
      kind: "payment_required",
      title: "결제 직전까지 예매되었습니다",
    });
    expect(legacyWithoutOutcome).toBeNull();
    expect(legacyWithoutActionability).toBeNull();
    expect(nonActionable).toBeNull();
    expect(malformedOutcome).toBeNull();
    expect(malformedActionability).toBeNull();
  });

  it("keeps an expired reconciled unknown as manual recovery despite payment evidence", () => {
    const expiredUnknown: WatchLifecycleSnapshot = {
      ...watch,
      status: "expired",
      latestReservationAttemptCandidateId: "candidate",
      latestReservationAttempt: {
        outcome: "unknown",
        resultReasonCode: "reservation_request_result_unknown",
        startedAt: "2026-08-03T12:09:45Z",
        finishedAt: "2026-08-03T12:09:48Z",
        retryable: false,
        manualCheckRequired: true,
        retryCondition: null,
        paymentHoldEndedAt: null,
      },
    };
    const notice = buildLiveReservationNotice({
      id: "reconciled-expired-unknown",
      event_type: "watch.reservation_reconciled",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:15:01Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        outcome: "unknown",
        payment_actionable: false,
        result_reason_code: "reservation_request_result_unknown",
        confirmation_outcome: "confirmed_payment_required",
        confirmation_observed_at: "2026-08-03T12:15:00Z",
        reconciliation_attempt_count: 2,
        next_reconcile_at: null,
      },
    }, [expiredUnknown]);

    expect(notice).toMatchObject({
      kind: "manual_check",
      title: "예매 요청 결과를 확인해야 합니다",
    });
    expect(notice?.description).toContain("공식 예약 내역에서 결제가 필요한 임시 예약을 확인했습니다");
    expect(notice?.description).toContain("감시는 종료되었습니다");
    expect(notice?.kind).not.toBe("payment_required");
  });

  it("rejects an unrecognized result reason instead of exposing provider text", () => {
    expect(buildLiveReservationNotice({
      id: "unsafe-result-reason",
      event_type: "watch.reservation_result",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:10:01Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        outcome: "unknown",
        retryable: false,
        manual_check_required: true,
        result_reason_code: "provider said card 1234 failed",
      },
    }, [watch])).toBeNull();
  });

  it("does not let a non-actionable reconciliation replace the terminal hold-ended notice", () => {
    const endedPaymentWatch: WatchLifecycleSnapshot = {
      ...watch,
      status: "watching",
      latestReservationAttemptCandidateId: "candidate",
      latestReservationAttempt: {
        outcome: "payment_required",
        resultReasonCode: "payment_hold_created",
        startedAt: "2026-08-03T12:09:45.851Z",
        finishedAt: "2026-08-03T12:09:48.250Z",
        retryable: false,
        manualCheckRequired: false,
        retryCondition: null,
        paymentHoldEndedAt: "2026-08-03T12:20:01.100Z",
        paymentHoldEndReason: "confirmed_payment_deadline_elapsed",
        confirmationOutcome: "confirmed_payment_required",
        confirmationObservedAt: "2026-08-03T12:20:01.100Z",
        reconciliationAttemptCount: 3,
        nextReconcileAt: null,
      },
    };
    const terminal = buildLiveReservationNotice({
      id: "hold-ended-terminal",
      event_type: "watch.payment_hold_ended_monitoring_resumed",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:20:01.250Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        terminal: true,
        status: "watching",
        from: "payment_required",
        to: "watching",
        reason: "confirmed_payment_deadline_elapsed",
        automatic_reservation_retry: true,
      },
    }, [endedPaymentWatch]);
    const nonActionableReconciliation = buildLiveReservationNotice({
      id: "reconciled-after-hold-ended",
      event_type: "watch.reservation_reconciled",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:20:01.300Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        outcome: "payment_required",
        payment_actionable: false,
        result_reason_code: "payment_hold_created",
        confirmation_outcome: "confirmed_payment_required",
        confirmation_observed_at: "2026-08-03T12:20:01.100Z",
        reconciliation_attempt_count: 3,
        next_reconcile_at: null,
      },
    }, [endedPaymentWatch]);

    expect(terminal).toMatchObject({
      kind: "recovery",
      title: "결제 가능 기한이 지났습니다",
    });
    expect(nonActionableReconciliation).toBeNull();
  });

  it("replaces payment progress with a terminal cancellation notice after confirmed hold expiry", () => {
    const notice = buildLiveReservationNotice({
      id: "hold-ended-event",
      event_type: "watch.payment_hold_ended_monitoring_resumed",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:20:01.250Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        terminal: true,
        status: "watching",
        from: "payment_required",
        to: "watching",
        reason: "confirmed_payment_deadline_elapsed",
        automatic_reservation_retry: true,
      },
    }, [{
      ...watch,
      status: "watching",
      latestReservationAttempt: {
        startedAt: "2026-08-03T12:09:45.851Z",
        finishedAt: "2026-08-03T12:09:48.250Z",
        paymentHoldEndedAt: "2026-08-03T12:20:01.100Z",
        paymentHoldEndReason: "confirmed_payment_deadline_elapsed",
      },
    }]);

    expect(notice).toMatchObject({
      subjectKey: `watch:${watch.id}`,
      revisionKey: `watch:${watch.id}:hold-ended-event`,
      revisionAt: "2026-08-03T12:20:01.250Z",
      occurredAt: "2026-08-03T12:20:01.250Z",
      kind: "recovery",
      tone: "warning",
      title: "결제 가능 기한이 지났습니다",
      meta: "KORAIL · 9248 · 일반실",
      description: expect.stringContaining("8월 4일 (화) · 대전 → 서울 · 17:50 → 18:58"),
    });
    expect(notice?.description).toContain("감시는 다시 시작되며");
    expect(notice?.steps?.map((step) => [step.label, step.state])).toEqual([
      ["좌석 발견", "completed"],
      ["자동 예매 요청 시작", "completed"],
      ["좌석 임시 확보", "completed"],
      ["결제 가능 시간 종료", "failed"],
      ["결제 가능 시간 종료 확인", "completed"],
      ["감시 재개", "completed"],
    ]);
    expect(notice?.steps?.some((step) => (
      step.state === "active" || step.state === "pending"
    ))).toBe(false);
  });

  it("builds a fixed completion notice from a typed paid event and preserves its revision", () => {
    const paymentRequiredWatch: WatchLifecycleSnapshot = {
      ...watch,
      status: "payment_required",
      latestReservationAttemptCandidateId: "candidate",
      latestReservationAttempt: {
        outcome: "payment_required",
        startedAt: "2026-08-03T12:09:45.851Z",
        finishedAt: "2026-08-03T12:09:48.250Z",
        retryable: false,
        manualCheckRequired: false,
        retryCondition: null,
        progressStages: [
          { stage: "authenticated_session_ready", occurredAt: "2026-08-03T12:09:46.100Z" },
          { stage: "target_rechecked", occurredAt: "2026-08-03T12:09:47.900Z" },
          { stage: "seat_selected", occurredAt: "2026-08-03T12:09:48.000Z" },
          { stage: "reservation_requested", occurredAt: "2026-08-03T12:09:48.100Z" },
        ],
        paymentHoldEndedAt: null,
      },
      updatedAt: "2026-08-03T12:09:48.250Z",
    };
    const notice = buildLiveReservationNotice({
      id: "payment-completed-event",
      event_type: "watch.payment_completed",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:21:01.250Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        terminal: true,
        status: "completed",
        from: "payment_required",
        to: "completed",
        automatic_reservation_retry: false,
        reason: "confirmed_paid",
        message: "원문 메시지는 표시하지 않습니다.",
      },
    }, [paymentRequiredWatch]);

    expect(notice).toMatchObject({
      subjectKey: `watch:${watch.id}`,
      revisionKey: `watch:${watch.id}:payment-completed-event`,
      revisionAt: "2026-08-03T12:21:01.250Z",
      occurredAt: "2026-08-03T12:21:01.250Z",
      kind: "recovery",
      tone: "success",
      title: "결제가 완료되었습니다",
      meta: "KORAIL · 9248 · 일반실",
      startedAt: "2026-08-03T12:09:45.851Z",
      durationMs: 2_399,
    });
    expect(notice?.description).toContain("공식 예약 내역에서 결제 완료를 확인했습니다");
    expect(notice?.description).not.toContain("원문 메시지");
    expect(notice?.steps?.find((step) => step.label === "검색 결과·열차 재확인"))
      .toMatchObject({ occurredAt: "2026-08-03T12:09:47.900Z", state: "completed" });
    expect(notice?.steps?.find((step) => step.label === "좌석 임시 확보"))
      .toMatchObject({ occurredAt: "2026-08-03T12:09:48.250Z", state: "completed" });
    expect(notice?.steps?.at(-1)).toMatchObject({
      label: "공식 결제 완료 확인",
      occurredAt: "2026-08-03T12:21:01.250Z",
      state: "completed",
    });

    const malformed = buildLiveReservationNotice({
      id: "malformed-payment-completed-event",
      event_type: "watch.payment_completed",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:21:02Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        terminal: true,
        status: "completed",
        from: "payment_required",
        to: "completed",
        reason: "confirmed_paid",
        automatic_reservation_retry: true,
      },
    }, [paymentRequiredWatch]);
    expect(malformed).toBeNull();

    const missingAttemptContext = buildLiveReservationNotice({
      id: "payment-completed-without-attempt",
      event_type: "watch.payment_completed",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:21:02Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        terminal: true,
        status: "completed",
        from: "payment_required",
        to: "completed",
        reason: "confirmed_paid",
        automatic_reservation_retry: false,
      },
    }, [watch]);
    expect(missingAttemptContext).toBeNull();

    const watchingUnknownWatch: WatchLifecycleSnapshot = {
      ...paymentRequiredWatch,
      status: "watching",
      latestReservationAttempt: {
        outcome: "unknown",
        startedAt: "2026-08-03T12:09:45.851Z",
        finishedAt: "2026-08-03T12:09:48.250Z",
        retryable: false,
        manualCheckRequired: true,
        retryCondition: null,
        progressStages: [
          { stage: "authenticated_session_ready", occurredAt: "2026-08-03T12:09:46.100Z" },
          { stage: "target_rechecked", occurredAt: "2026-08-03T12:09:47.900Z" },
          { stage: "seat_selected", occurredAt: "2026-08-03T12:09:48.000Z" },
          { stage: "reservation_requested", occurredAt: "2026-08-03T12:09:48.100Z" },
        ],
        paymentHoldEndedAt: null,
      },
    };
    const paidAfterUnknown = buildLiveReservationNotice({
      id: "payment-completed-after-unknown",
      event_type: "watch.payment_completed",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:21:03Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        terminal: true,
        status: "completed",
        from: "watching",
        to: "completed",
        reason: "confirmed_paid",
        automatic_reservation_retry: false,
      },
    }, [watchingUnknownWatch]);
    expect(paidAfterUnknown).toMatchObject({
      revisionKey: `watch:${watch.id}:payment-completed-after-unknown`,
      title: "결제가 완료되었습니다",
    });

    for (const payloadOverride of [
      { from: "scheduled" },
      { reason: "raw-provider-reason" },
      { terminal: false },
      { to: "watching" },
      { status: "watching" },
      { automatic_reservation_retry: true },
    ]) {
      expect(buildLiveReservationNotice({
        id: `rejected-payment-completed-${Object.keys(payloadOverride)[0]}`,
        event_type: "watch.payment_completed",
        aggregate_id: watch.id,
        created_at: "2026-08-03T12:21:04Z",
        payload: {
          watch_id: watch.id,
          candidate_id: "candidate",
          terminal: true,
          status: "completed",
          from: "watching",
          to: "completed",
          reason: "confirmed_paid",
          automatic_reservation_retry: false,
          ...payloadOverride,
        },
      }, [watchingUnknownWatch])).toBeNull();
    }
  });

  it("rejects a seat detection time after the automatic request started", () => {
    const notice = buildLiveReservationNotice({
      id: "attempt-with-future-detection",
      event_type: "watch.reservation_attempted",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:09:45Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        attempt_started_at: "2026-08-03T12:09:45Z",
        seat_detected_at: "2026-08-03T12:09:46Z",
      },
    }, [watch]);

    expect(notice?.steps?.some((step) => step.label === "좌석 발견")).toBe(false);
    expect(notice?.steps?.find((step) => step.label === "자동 예매 요청 시작"))
      .not.toHaveProperty("durationMs");
  });

  it("ends a one-off task and rejects a malformed hold-ended event", () => {
    const terminal = buildLiveReservationNotice({
      id: "hold-ended-terminal-event",
      event_type: "watch.payment_hold_ended_one_off_expired",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:20:01.250Z",
      payload: {
        watch_id: watch.id,
        candidate_id: "candidate",
        terminal: true,
        status: "expired",
        from: "payment_required",
        to: "expired",
        reason: "confirmed_payment_hold_no_longer_present",
        automatic_reservation_retry: false,
      },
    }, [watch]);
    const malformed = buildLiveReservationNotice({
      id: "hold-ended-malformed-event",
      event_type: "watch.payment_hold_ended_one_off_expired",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:20:02Z",
      payload: {
        watch_id: watch.id,
        terminal: true,
        status: "expired",
        from: "payment_required",
        to: "expired",
        reason: "confirmed_payment_deadline_elapsed",
        automatic_reservation_retry: true,
      },
    }, [watch]);

    expect(terminal?.title).toBe("공식 내역에서 대상 임시 예약을 더 이상 찾지 못했습니다");
    expect(terminal?.description).not.toContain("결제되지");
    expect(terminal?.steps).toContainEqual(expect.objectContaining({
      label: "공식 내역 목록 부재 확인",
      state: "completed",
    }));
    expect(terminal?.description).toContain("1회 알림 작업은 종료되었습니다");
    expect(terminal?.steps?.at(-1)).toEqual({
      label: "작업 종료",
      state: "completed",
      occurredAt: "2026-08-03T12:20:01.250Z",
    });
    expect(malformed).toBeNull();
  });
});
