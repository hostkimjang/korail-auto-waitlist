import { describe, expect, it } from "vitest";

import { buildLiveReservationNotice } from "../src/features/app/liveReservationNotice";
import type { WatchSnapshot } from "../src/features/app/watchSnapshots";

const watch: WatchSnapshot = {
  id: "watch-korail-9248",
  status: "watching",
  provider: "KORAIL",
  route: "대전 → 서울",
  train: "9248",
  seatClassLabel: "일반실",
  date: "8월 4일 (화)",
  departure: "17:50",
  arrival: "18:58",
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
};

describe("live reservation notices", () => {
  it("builds reserving directly from the attempted SSE without a REST reserving snapshot", () => {
    const notice = buildLiveReservationNotice({
      id: "attempt-event",
      event_type: "watch.reservation_attempted",
      aggregate_id: watch.id,
      created_at: "2026-08-03T12:09:45.851Z",
      payload: { watch_id: watch.id, candidate_id: "candidate", outcome: "pending" },
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
      ["예매 시작", "active"],
      ["로그인 세션 확인", "pending"],
      ["열차·좌석 재확인", "pending"],
      ["좌석 선택", "pending"],
      ["예약 요청", "pending"],
      ["공식 결과 확인", "pending"],
    ]);
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
        occurredAt: "2026-08-03T12:09:45.851Z",
      },
      {
        label: "예매 시작",
        state: "completed",
        occurredAt: "2026-08-03T12:09:45.851Z",
        durationMs: 0,
        durationPrefix: "대기",
      },
      {
        label: "로그인 세션 확인",
        state: "completed",
        occurredAt: "2026-08-03T12:09:46.100Z",
        durationMs: 249,
        durationPrefix: "처리",
      },
      {
        label: "열차·좌석 재확인",
        state: "failed",
        occurredAt: "2026-08-03T12:09:47.900Z",
        durationMs: 1_800,
        durationPrefix: "처리",
      },
      {
        label: "공식 결과 확인",
        state: "completed",
        occurredAt: "2026-08-03T12:09:48.250Z",
        durationMs: 350,
        durationPrefix: "처리",
      },
      {
        label: "감시·재예매 대기",
        state: "active",
        occurredAt: "2026-08-03T12:09:48.367Z",
      },
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
        outcome: "payment_required",
        startedAt: "2026-08-03T12:09:45.851Z",
        finishedAt: "2026-08-03T12:09:48.250Z",
        paymentHoldEndedAt: "2026-08-03T12:20:01.100Z",
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
      ["예매 시작", "completed"],
      ["좌석 임시 확보", "completed"],
      ["결제기한 내 결제 미완료", "failed"],
      ["결제 가능 시간 종료 확인", "completed"],
      ["감시 재개", "completed"],
    ]);
    expect(notice?.steps?.some((step) => (
      step.state === "active" || step.state === "pending"
    ))).toBe(false);
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
