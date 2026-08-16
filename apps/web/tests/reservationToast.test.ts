import { describe, expect, it } from "vitest";

import {
  buildWatchActionToast,
  buildReservationRecoveryToast,
  type ReservationRecoveryResult,
} from "../src/features/app/reservationToast";
import type { WatchActionTransition } from "../src/features/app/watchSnapshots";

const transition: WatchActionTransition = {
  id: "watch-122",
  status: "monitoring_resumed",
  provider: "KORAIL",
  route: "대전 → 서울",
  train: "KTX 00122",
  seatClassLabel: "일반실",
  date: "8월 3일 (월)",
  departure: "09:51",
  arrival: "11:34",
};

function result(overrides: Partial<ReservationRecoveryResult>): ReservationRecoveryResult {
  return {
    outcome: "unknown",
    retryable: false,
    manualCheckRequired: true,
    retryCondition: null,
    resultReasonCode: null,
    confirmationOutcome: null,
    confirmationDiagnosticCode: null,
    confirmationObservedAt: null,
    reconciliationAttemptCount: 0,
    nextReconcileAt: null,
    ...overrides,
  };
}

describe("reservation recovery toast", () => {
  it("adds confirmed seat assignments to a payment notice and omits absent values", () => {
    const confirmed = buildWatchActionToast({
      ...transition,
      status: "payment_required",
      reservedSeats: [{ carNumber: "3", seatNumber: "12A" }],
    });
    const absent = buildWatchActionToast({ ...transition, status: "payment_required" });

    expect(confirmed.meta).toContain("예약 좌석 3호차 12A");
    expect(confirmed.description).toContain("예약 좌석 3호차 12A");
    expect(absent.meta).not.toContain("예약 좌석");
    expect(absent.description).not.toContain("예약 좌석");
  });

  it("uses actual result timestamps for clear deltas and the completed total", () => {
    const toast = buildWatchActionToast({
      ...transition,
      status: "payment_required",
      detectedAt: "2026-08-03T12:09:44.500Z",
      startedAt: "2026-08-03T12:09:45.000Z",
      finishedAt: "2026-08-03T12:09:48.250Z",
      revisionAt: "2026-08-03T12:09:48.367Z",
      reservationProgress: [
        { stage: "authenticated_session_ready", occurredAt: "2026-08-03T12:09:46.100Z" },
        { stage: "target_rechecked", occurredAt: "2026-08-03T12:09:47.900Z" },
      ],
    });

    expect(toast.durationMs).toBe(3_250);
    expect(toast.steps?.slice(0, 5)).toEqual([
      {
        label: "좌석 발견",
        state: "completed",
        occurredAt: "2026-08-03T12:09:44.500Z",
      },
      {
        label: "자동 예매 요청 시작",
        state: "completed",
        occurredAt: "2026-08-03T12:09:45.000Z",
        durationMs: 500,
        durationPrefix: "감지 후",
      },
      {
        label: "로그인 세션 확인",
        state: "completed",
        occurredAt: "2026-08-03T12:09:46.100Z",
        durationMs: 1_100,
        durationPrefix: "이전 단계 후",
      },
      {
        label: "검색 결과·열차 재확인",
        state: "completed",
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
    ]);
  });

  it("does not invent a completed total without a valid finish time", () => {
    const reserving = buildWatchActionToast({
      ...transition,
      status: "reserving",
      startedAt: "2026-08-03T12:09:45Z",
      finishedAt: "2026-08-03T12:09:46Z",
      revisionAt: "2026-08-03T12:09:46Z",
    });
    const invalidTerminal = buildWatchActionToast({
      ...transition,
      status: "failed",
      startedAt: "2026-08-03T12:09:48Z",
      finishedAt: "2026-08-03T12:09:45Z",
      revisionAt: "2026-08-03T12:09:49Z",
    });

    expect(reserving.durationMs).toBeNull();
    expect(invalidTerminal.durationMs).toBeNull();
  });

  it("announces a future retry only for a conclusively retryable no-seat result", () => {
    const toast = buildReservationRecoveryToast({
      ...transition,
      detectedAt: "2026-08-03T12:09:44Z",
      startedAt: "2026-08-03T12:09:45Z",
      finishedAt: "2026-08-03T12:09:48Z",
      revisionAt: "2026-08-03T12:09:48Z",
    }, result({
      outcome: "not_available",
      retryable: true,
      manualCheckRequired: false,
      retryCondition: "new_availability_episode",
    }));

    expect(toast.title).toBe("좌석이 사라져 다시 감시 중입니다");
    expect(toast.description).toContain("좌석이 다시 확인되면 예매를 다시 시도합니다");
    expect(toast.steps).toEqual([
      {
        label: "좌석 발견",
        state: "completed",
        occurredAt: "2026-08-03T12:09:44Z",
      },
      {
        label: "자동 예매 요청 시작",
        state: "completed",
        occurredAt: "2026-08-03T12:09:45Z",
        durationMs: 1_000,
        durationPrefix: "감지 후",
      },
      {
        label: "좌석 재확인",
        state: "failed",
        occurredAt: "2026-08-03T12:09:48Z",
        durationPrefix: "이전 단계 후",
        showNoticeDuration: true,
      },
      {
        label: "감시·재예매 대기",
        state: "active",
        occurredAt: "2026-08-03T12:09:48Z",
      },
    ]);
  });

  it.each([
    result({ outcome: "unknown" }),
    result({ outcome: "failed", manualCheckRequired: false }),
  ])("keeps ambiguous or non-retryable results in manual-check mode", (reservationResult) => {
    const toast = buildReservationRecoveryToast(transition, reservationResult);

    expect(toast.title).toBe("예매 결과를 확인해야 합니다");
    expect(toast.description).toContain("자동 재예매를 보류합니다");
    expect(toast.description).toContain("공식 예약 내역을 확인해 주세요");
    expect(toast.description).not.toContain("예매를 다시 시도합니다");
    expect(toast.steps?.at(-1)).toEqual({ label: "감시·수동 확인", state: "active" });
  });

  it.each([
    [
      "delay_consent_required",
      "운행 지연 동의가 필요합니다",
      "철도사 지연 안내창에서 운행 지연 동의가 필요합니다.",
    ],
    [
      "existing_reservation_action_required",
      "기존 예약 안내를 확인해야 합니다",
      "철도사 기존 예약 안내창에서 진행할 예약을 선택해야 합니다.",
    ],
    [
      "provider_notice_action_required",
      "철도사 안내창 확인이 필요합니다",
      "철도사 안내창에서 사용자 확인이 필요합니다.",
    ],
  ] as const)("separates the %s provider action without raw dialog text", (
    resultReasonCode,
    title,
    detail,
  ) => {
    const toast = buildReservationRecoveryToast(transition, result({
      resultReasonCode,
      nextReconcileAt: "2026-08-03T12:14:00Z",
    }));

    expect(toast.title).toBe(title);
    expect(toast.description).toContain(detail);
    expect(toast.description).not.toContain("raw-provider-dialog");
  });

  it("explains a post-click provider outage without dropping the manual-check fence", () => {
    const toast = buildReservationRecoveryToast(transition, result({
      resultReasonCode: "provider_unavailable",
      confirmationOutcome: "inconclusive",
      reconciliationAttemptCount: 1,
    }));

    expect(toast.title).toBe("철도사 연결 문제로 예매 결과를 확인해야 합니다");
    expect(toast.description).toContain("연결 또는 응답 확인에 실패했습니다");
    expect(toast.description).toContain("자동 재예매를 보류합니다");
    expect(toast.description).toContain("공식 예약 내역을 확인해 주세요");
    expect(toast.description).not.toMatch(/결제 (실패|완료)/);
  });

  it.each([
    ["not_found", "공식 예약 내역에서 대상 예약을 찾지 못했습니다."],
    ["inconclusive", "공식 예약 내역 확인으로 결과를 확정하지 못했습니다."],
  ] as const)("shows official confirmation %s without guessing payment state", (
    confirmationOutcome,
    detail,
  ) => {
    const toast = buildReservationRecoveryToast(transition, result({
      resultReasonCode: "reservation_request_result_unknown",
      confirmationOutcome,
      confirmationObservedAt: "2026-08-03T12:13:00Z",
      reconciliationAttemptCount: 4,
      nextReconcileAt: "2026-08-03T12:14:00Z",
    }));

    expect(toast.description).toContain(detail);
    expect(toast.description).toContain("공식 내역 자동 재확인 4/6회 수행");
    expect(toast.description).toContain("다음 자동 재확인은 21:14 예정");
    expect(toast.description).not.toMatch(/결제 (미완료|완료)/);
    expect(toast.steps?.at(-1)?.label).toBe("공식 결과 자동 재확인 대기");
  });

  it.each([
    [
      "official_read_unavailable",
      "철도사 공식 내역을 불러오거나 응답을 확인하지 못했습니다.",
    ],
    [
      "credential_context_mismatch",
      "예매 시도와 공식 확인의 계정 상태가 달라 결과를 연결하지 못했습니다.",
    ],
    [
      "official_record_ambiguous",
      "공식 내역에서 이번 예매 시도와 정확히 일치하는 항목을 하나로 구분하지 못했습니다.",
    ],
    [
      "official_evidence_insufficient",
      "공식 내역은 확인했지만 예약 상태를 확정할 정보가 충분하지 않습니다.",
    ],
    [
      "unspecified",
      "공식 예약 내역 확인으로 결과를 확정하지 못했습니다.",
    ],
  ] as const)("explains inconclusive confirmation diagnostic %s without inferring payment", (
    confirmationDiagnosticCode,
    detail,
  ) => {
    const toast = buildReservationRecoveryToast(transition, result({
      resultReasonCode: "reservation_request_result_unknown",
      confirmationOutcome: "inconclusive",
      confirmationDiagnosticCode,
    }));

    expect(toast.description).toContain(detail);
    expect(toast.description).toContain("공식 예약 내역을 확인해 주세요");
    expect(toast.description).not.toMatch(/결제 (실패|취소|완료)/);
  });

  it("keeps provider blocking separate from an authentication failure", () => {
    const toast = buildWatchActionToast({
      ...transition,
      status: "auth_required",
      resultReasonCode: "provider_blocked",
      confirmationOutcome: "provider_blocked",
      confirmationObservedAt: "2026-08-03T12:13:00Z",
      reconciliationAttemptCount: 1,
      nextReconcileAt: null,
    });

    expect(toast.title).toBe("운영사 요청 제한으로 확인이 필요합니다");
    expect(toast.description).toContain("운영사 제한으로 공식 예약 내역을 확인하지 못했습니다");
    expect(toast.description).not.toContain("철도 계정을 다시 확인");
  });

  it("keeps an expired unknown result visible without claiming monitoring resumed", () => {
    const toast = buildReservationRecoveryToast(
      { ...transition, monitoringResumed: false },
      result({ outcome: "unknown" }),
    );

    expect(toast.description).toContain("감시는 종료되었습니다");
    expect(toast.description).not.toContain("감시는 계속됩니다");
    expect(toast.steps?.at(-1)).toEqual({ label: "공식 결과 수동 확인", state: "active" });
  });

  it("describes a conclusively failed retryable attempt without one-shot wording", () => {
    const toast = buildReservationRecoveryToast(transition, result({
      outcome: "failed",
      retryable: true,
      manualCheckRequired: false,
      retryCondition: "new_availability_episode",
    }));

    expect(toast.title).toBe("예매에 실패해 다시 감시 중입니다");
    expect(toast.description).toContain("예약된 좌석은 없습니다");
    expect(toast.description).toContain("예매를 다시 시도합니다");
    expect(toast.description).not.toContain("다시 예매하지 않습니다");
  });
});
