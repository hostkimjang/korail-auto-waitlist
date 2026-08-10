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
    expect(notice?.steps?.some((step) => step.label === "좌석 선택")).toBe(false);
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
        progress_stages: [
          { stage: "authenticated_session_ready", occurred_at: "2026-08-10T13:01:18.506Z" },
          { stage: "target_rechecked", occurred_at: "2026-08-10T13:01:19.651Z" },
          { stage: "seat_selected", occurred_at: "2026-08-10T13:01:19.775Z" },
          { stage: "reservation_requested", occurred_at: "2026-08-10T13:01:19.799Z" },
        ],
      },
    }, [watch]);

    expect(notice?.durationMs).toBe(7_717);
    expect(notice?.steps?.map((step) => [step.label, step.durationMs])).toEqual([
      ["좌석 발견", undefined],
      ["자동 예매 요청 시작", undefined],
      ["로그인 세션 확인", 4_605],
      ["검색 결과·열차 재확인", 1_145],
      ["좌석 선택", 124],
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
      payload: { watch_id: watch.id, candidate_id: "candidate" },
    }, [watch]);

    expect(notice).toMatchObject({
      kind: "manual_check",
      title: "예매 결과를 확인해야 합니다",
      autoCloseMs: null,
    });
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
      title: "결제기한 안에 결제되지 않아 예매가 취소되었습니다",
      meta: "KORAIL · 9248 · 일반실",
      description: expect.stringContaining("8월 4일 (화) · 대전 → 서울 · 17:50 → 18:58"),
    });
    expect(notice?.description).toContain("감시는 다시 시작되며");
    expect(notice?.steps?.map((step) => [step.label, step.state])).toEqual([
      ["좌석 발견", "completed"],
      ["자동 예매 요청 시작", "completed"],
      ["좌석 임시 확보", "completed"],
      ["결제기한 내 결제 미완료", "failed"],
      ["결제 가능 시간 종료 확인", "completed"],
      ["감시 재개", "completed"],
    ]);
    expect(notice?.steps?.some((step) => (
      step.state === "active" || step.state === "pending"
    ))).toBe(false);
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

    expect(terminal?.title).toBe("공식 확인 결과 임시 예약이 종료되었습니다");
    expect(terminal?.description).toContain("1회 알림 작업은 종료되었습니다");
    expect(terminal?.steps?.at(-1)).toEqual({
      label: "작업 종료",
      state: "completed",
      occurredAt: "2026-08-03T12:20:01.250Z",
    });
    expect(malformed).toBeNull();
  });
});
