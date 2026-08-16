import { describe, expect, it } from "vitest";

import type { ProjectedWatch } from "../src/api/watchProjection";
import {
  activeWatchRefreshLabel,
  mapActiveWatch,
  presentActiveWatchRow,
  type ActiveWatch,
} from "../src/features/home/activeWatchViewModel";

function mappedWatch(overrides: Partial<ProjectedWatch> = {}): ProjectedWatch {
  return {
    id: "watch-1",
    provider: "KORAIL",
    status: "watching",
    candidates: [],
    payment_deadline: null,
    created_at: null,
    updated_at: null,
    official_booking_url: "https://www.korail.com/ticket/search/general",
    reservation_policy: "notify_only",
    paymentDeadline: null,
    createdAt: null,
    updatedAt: null,
    train: "KTX 101",
    route: "서울 → 부산",
    departure: "12:00",
    arrival: "14:30",
    date: "8월 8일 (토)",
    statusLabel: "감시 중",
    seatClass: "standard",
    seatClassLabel: "일반실",
    seatEvidenceLabel: "일반실 · 매진 · 공식 관측 11:55",
    registrationEvidenceLabel: "일반실 · 매진 · 공식 관측 11:50",
    activityLabel: "일반실 · 매진 · 공식 관측 11:55",
    lastCheckedAt: "2026-08-08T02:55:00Z",
    lastCheckedLabel: "최근 확인 11:55",
    origin: "서울",
    destination: "부산",
    travelDate: "2026-08-08",
    officialBookingUrl: "https://www.korail.com/ticket/search/general",
    operational: null,
    latestReservationAttempt: null,
    paymentRequiredReservedSeats: [],
    seatFoundObservation: null,
    reservationCandidateContexts: {},
    reservationPolicy: "notify_only",
    seatObservationMode: "balanced",
    focusedObservationIntervalSeconds: 25,
    nextCheckAt: null,
    observationExecutionState: "idle",
    ...overrides,
  };
}

function activeWatch(overrides: Partial<ActiveWatch> = {}): ActiveWatch {
  return {
    ...mapActiveWatch(mappedWatch(), "authenticated"),
    latestReservationAttemptCandidateId: "candidate-1",
    latestReservationAttemptContext: {
      candidateId: "candidate-1",
      provider: "KORAIL",
      train: "KTX 101",
      trainType: null,
      date: "8월 8일 (토)",
      departure: "12:00",
      arrival: "14:30",
      seatClass: "standard",
      seatClassLabel: "일반실",
    },
    ...overrides,
  };
}

const ATTEMPT_CONTEXT_LABEL =
  "예매 대상 · KORAIL · KTX 101 · 8월 8일 (토) · 12:00 출발 · 14:30 도착 · 일반실";

describe("active watch view model", () => {
  it("maps the API read model into the narrow Home contract", () => {
    const mapped = mapActiveWatch(mappedWatch({
      seatFoundObservation: {
        kind: "official_provider",
        source: "korail-browser",
        observedAt: "2026-08-08T03:00:00Z",
        observedLabel: "최근 확인 12:00",
      },
    }), "authenticated");

    expect(mapped).toMatchObject({
      id: "watch-1",
      route: "서울 → 부산",
      accountAuthStatus: "authenticated",
      seatFoundObservation: {
        kind: "official_provider",
        observedAt: "2026-08-08T03:00:00Z",
        observedLabel: "최근 확인 12:00",
      },
    });
    expect(mapped.seatFoundObservation).not.toHaveProperty("source");
  });

  it("drops an attempt context unless both its candidate and provider match exactly", () => {
    const latestReservationAttempt = {
      outcome: "unknown" as const,
      startedAt: "2026-08-08T13:00:00Z",
      finishedAt: "2026-08-08T13:01:00Z",
      retryable: false,
      manualCheckRequired: true,
      retryCondition: null,
      paymentHoldEndedAt: null,
    };
    const context = {
      candidateId: "candidate-2",
      provider: "KORAIL" as const,
      train: "326",
      trainType: "KTX-산천",
      date: "8월 8일 (토)",
      departure: "14:11",
      arrival: "16:52",
      seatClass: "first" as const,
      seatClassLabel: "특실",
    };
    const candidateMismatch = mapActiveWatch(mappedWatch({
      latestReservationAttempt,
      latestReservationAttemptCandidateId: "candidate-1",
      latestReservationAttemptContext: context,
    }), "authenticated");
    const providerMismatch = mapActiveWatch(mappedWatch({
      latestReservationAttempt,
      latestReservationAttemptCandidateId: "candidate-2",
      latestReservationAttemptContext: { ...context, provider: "SRT" },
    }), "authenticated");

    expect(candidateMismatch.latestReservationAttemptContext).toBeNull();
    expect(providerMismatch.latestReservationAttemptContext).toBeNull();
  });

  it.each([
    ["pending", false, false, null, `${ATTEMPT_CONTEXT_LABEL} · 예매 시도 중 · 22:00:00`],
    ["payment_required", false, false, null, `${ATTEMPT_CONTEXT_LABEL} · 좌석 임시 확보 · 결제 필요 · 22:00:00`],
    ["not_available", true, false, "new_availability_episode", `${ATTEMPT_CONTEXT_LABEL} · 예매 시도 · 좌석 확보 실패 · 감시 계속 · 22:00:00 · 매진 후 좌석이 다시 열리면 자동 예매`],
    ["auth_required", true, false, "provider_account_reverified", `${ATTEMPT_CONTEXT_LABEL} · 예매 시도 · 철도 계정 재확인 필요 · 22:00:00`],
    ["provider_blocked", false, false, null, `${ATTEMPT_CONTEXT_LABEL} · 예매 시도 · 운영사 제한 · 자동 재확인 대기 · 22:00:00`],
    ["unknown", false, true, null, `${ATTEMPT_CONTEXT_LABEL} · 예매 시도 결과 확인 필요 · 22:00:00 · 공식 예매 내역을 확인해 주세요`],
  ] as const)("preserves %s reservation-attempt copy", (outcome, retryable, manualCheckRequired, retryCondition, expected) => {
    const presentation = presentActiveWatchRow(activeWatch({
      latestReservationAttempt: {
        outcome,
        startedAt: "2026-08-08T13:00:00Z",
        finishedAt: null,
        retryable,
        manualCheckRequired,
        retryCondition,
        paymentHoldEndedAt: null,
      },
    }), false);

    expect(presentation.reservationAttemptLabel).toBe(expected);
  });

  it("keeps hold-end, provider-blocked, and policy switch fail-closed presentation", () => {
    const endedHold = presentActiveWatchRow(activeWatch({
      status: "watching",
      statusLabel: "감시 중",
      reservationPolicy: "reserve_once_before_payment",
      latestReservationAttempt: {
        outcome: "payment_required",
        startedAt: "2026-08-08T13:00:00Z",
        finishedAt: "2026-08-08T13:01:00Z",
        retryable: true,
        manualCheckRequired: false,
        retryCondition: "new_availability_episode",
        paymentHoldEndedAt: "2026-08-08T13:02:00Z",
        paymentHoldEndReason: "confirmed_payment_hold_no_longer_present",
        manualRearmAvailable: true,
        manualRearmReason: "payment_hold_ended",
      },
    }), false);
    const blocked = presentActiveWatchRow(mapActiveWatch(mappedWatch({
      status: "auth_required",
      statusLabel: "로그인 필요",
    }), "provider_blocked"), false);
    const reserving = presentActiveWatchRow(activeWatch({ status: "reserving" }), false);

    expect(endedHold.statusLabel).toBe("대상 임시 예약 목록 부재 · 감시 중");
    expect(endedHold.reservationAttemptLabel).toBe(
      `${ATTEMPT_CONTEXT_LABEL} · 공식 내역에서 대상 임시 예약을 더 이상 찾지 못함 · 22:02:00 · 다시 시도하려면 사용자 확인 필요`,
    );
    expect(endedHold.canManualRearmReservation).toBe(true);
    expect(blocked).toMatchObject({
      statusLabel: "운영사 제한 · 자동 재확인 대기",
      authSummary: "저장된 계정으로 운영사 세션을 자동 재확인 중",
      policySwitchDisabled: true,
      shouldShowPolicyAccountLink: true,
      policyAccountLinkLabel: "운영사 상태 확인",
    });
    expect(reserving).toMatchObject({
      policySwitchDisabled: true,
      policySwitchTitle: "예약 시도가 시작된 뒤에는 실행 방식을 변경할 수 없습니다.",
    });
  });

  it.each([
    [
      "auth_required",
      "로그인 필요",
      "KORAIL 계정 재확인 필요",
      false,
      "공식 내역 확인에 로그인 필요",
    ],
    [
      "provider_blocked",
      "운영사 제한 · 자동 재확인 대기",
      "저장된 계정으로 운영사 세션을 자동 재확인 중",
      true,
      "운영사 제한으로 공식 내역 확인 불가",
    ],
  ] as const)("prioritizes UNKNOWN reconciliation %s over a stale authenticated account", (
    confirmationOutcome,
    statusLabel,
    authSummary,
    isProviderReverificationPending,
    evidence,
  ) => {
    const presentation = presentActiveWatchRow(activeWatch({
      status: "auth_required",
      statusLabel: "로그인 필요",
      accountAuthStatus: "authenticated",
      latestReservationAttempt: {
        outcome: "unknown",
        resultReasonCode: "reservation_request_result_unknown",
        startedAt: "2026-08-08T13:00:00Z",
        finishedAt: "2026-08-08T13:01:00Z",
        retryable: false,
        manualCheckRequired: true,
        retryCondition: null,
        paymentHoldEndedAt: null,
        confirmationOutcome,
        confirmationObservedAt: "2026-08-08T13:02:00Z",
        reconciliationAttemptCount: 2,
        nextReconcileAt: null,
      },
    }), false);

    expect(presentation).toMatchObject({
      statusLabel,
      authSummary,
      isAuthRequired: true,
      isProviderReverificationPending,
    });
    expect(presentation.reservationAttemptLabel).toContain(evidence);
  });

  it("shows a safe reason and official recheck evidence without guessing payment", () => {
    const presentation = presentActiveWatchRow(activeWatch({
      status: "watching",
      reservationPolicy: "reserve_once_before_payment",
      latestReservationAttempt: {
        outcome: "unknown",
        resultReasonCode: "existing_reservation_action_required",
        startedAt: "2026-08-08T13:00:00Z",
        finishedAt: "2026-08-08T13:01:00Z",
        retryable: false,
        manualCheckRequired: true,
        retryCondition: null,
        paymentHoldEndedAt: null,
        confirmationOutcome: "inconclusive",
        confirmationObservedAt: "2026-08-08T13:02:00Z",
        reconciliationAttemptCount: 6,
        reconciliationResolution: "exhausted_unresolved",
        nextReconcileAt: null,
        manualRearmAvailable: true,
        manualRearmReason: "unknown_result_unresolved",
      },
    }), false);

    expect(presentation.reservationAttemptLabel).toContain("기존 예약 안내 확인 필요");
    expect(presentation.reservationAttemptLabel).toContain(
      "공식 예약 내역 확인으로 결과를 확정하지 못했습니다.",
    );
    expect(presentation.reservationAttemptLabel).toContain("공식 재확인 6/6회");
    expect(presentation.reservationAttemptLabel).toContain("예약 결과 자동 확인 불가");
    expect(presentation.statusLabel).toBe("예약 결과 자동 확인 불가 · 감시 중");
    expect(presentation.canManualRearmReservation).toBe(true);
    expect(presentation.manualRearmReason).toBe("unknown_result_unresolved");
    expect(presentation.reservationAttemptLabel).not.toMatch(/결제 (미완료|완료)/);
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
  ] as const)("shows confirmation diagnostic %s in the active row without inferring payment", (
    confirmationDiagnosticCode,
    detail,
  ) => {
    const presentation = presentActiveWatchRow(activeWatch({
      status: "watching",
      latestReservationAttempt: {
        outcome: "unknown",
        startedAt: "2026-08-08T13:00:00Z",
        finishedAt: "2026-08-08T13:01:00Z",
        retryable: false,
        manualCheckRequired: true,
        retryCondition: null,
        paymentHoldEndedAt: null,
        confirmationOutcome: "inconclusive",
        confirmationDiagnosticCode,
      },
    }), false);

    expect(presentation.reservationAttemptLabel).toContain(detail);
    expect(presentation.reservationAttemptLabel).toContain("공식 예매 내역을 확인해 주세요");
    expect(presentation.reservationAttemptLabel).not.toMatch(/결제 (실패|취소|완료)/);
  });

  it("attributes a detailed diagnosis to the exact attempt candidate and fails closed without it", () => {
    const latestReservationAttempt: NonNullable<ActiveWatch["latestReservationAttempt"]> = {
      outcome: "unknown",
      resultReasonCode: "reservation_request_result_unknown",
      startedAt: "2026-08-08T13:00:00Z",
      finishedAt: "2026-08-08T13:01:00Z",
      retryable: false,
      manualCheckRequired: true,
      retryCondition: null,
      paymentHoldEndedAt: null,
      confirmationOutcome: "inconclusive",
      confirmationDiagnosticCode: "official_record_ambiguous",
    };
    const exact = presentActiveWatchRow(activeWatch({
      train: "KTX 033",
      latestReservationAttempt,
      latestReservationAttemptContext: {
        candidateId: "candidate-2",
        provider: "KORAIL",
        train: "326",
        trainType: "KTX-산천",
        date: "8월 8일 (토)",
        departure: "14:11",
        arrival: "16:52",
        seatClass: "first",
        seatClassLabel: "특실",
      },
    }), false);
    const missing = presentActiveWatchRow(activeWatch({
      train: "KTX 033",
      latestReservationAttempt,
      latestReservationAttemptContext: null,
    }), false);

    expect(exact.reservationAttemptLabel).toContain(
      "예매 대상 · KORAIL · KTX-산천 · 326 · 8월 8일 (토) · 14:11 출발 · 16:52 도착 · 특실",
    );
    expect(exact.reservationAttemptLabel).toContain(
      "공식 내역에서 이번 예매 시도와 정확히 일치하는 항목을 하나로 구분하지 못했습니다.",
    );
    expect(missing.reservationAttemptLabel).toBe(
      "예매 시도 대상 열차를 정확히 연결하지 못해 상세 결과를 표시하지 않습니다. 공식 예매 내역 전체를 확인해 주세요.",
    );
    expect(missing.reservationAttemptLabel).not.toContain("공식 내역에서 이번 예매 시도");
    expect(missing.reservationAttemptLabel).not.toContain("예약 요청 결과 불명확");
  });

  it("keeps the refresh placeholder and seat-found action gate explicit", () => {
    expect(activeWatchRefreshLabel(null)).toBe("최근 갱신 --:--:--");
    expect(presentActiveWatchRow(activeWatch({ status: "seat_found" }), false)
      .canRenderSeatFoundAction).toBe(false);
    expect(presentActiveWatchRow(activeWatch({
      status: "seat_found",
      seatFoundObservation: {
        kind: "official_provider",
        observedAt: "2026-08-08T03:00:00Z",
        observedLabel: "최근 확인 12:00",
      },
    }), false).canRenderSeatFoundAction).toBe(true);

    expect(presentActiveWatchRow(activeWatch({
      status: "watching",
      reservationPolicy: "reserve_once_before_payment",
      latestReservationAttempt: {
        outcome: "payment_required",
        startedAt: "2026-08-08T13:00:00Z",
        finishedAt: "2026-08-08T13:01:00Z",
        retryable: false,
        manualCheckRequired: false,
        retryCondition: null,
        paymentHoldEndedAt: "2026-08-08T13:02:00Z",
        paymentHoldEndReason: "confirmed_payment_hold_no_longer_present",
        manualRearmAvailable: true,
        manualRearmReason: "payment_hold_ended",
      },
      seatFoundObservation: {
        kind: "official_provider",
        observedAt: "2026-08-08T13:02:01Z",
        observedLabel: "최근 확인 22:02",
      },
    }), false)).toMatchObject({
      canRenderSeatFoundAction: true,
      canManualRearmReservation: true,
    });
  });

  it("shows an explicit observation state instead of exposing the internal claim as an ETA", () => {
    const inProgress = presentActiveWatchRow(activeWatch({
      nextCheckAt: "2026-08-08T03:01:00Z",
      observationExecutionState: "in_progress",
    }), false);
    const idle = presentActiveWatchRow(activeWatch({
      nextCheckAt: "2026-08-08T03:00:02Z",
      observationExecutionState: "idle",
    }), false);

    expect(inProgress.nextCheckLabel).toBe("좌석 관측 중");
    expect(idle.nextCheckLabel).toBe("다음 좌석 관측 목표 12:00:02");
  });
});
