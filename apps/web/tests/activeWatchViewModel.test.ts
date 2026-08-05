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
    seatFoundObservation: null,
    reservationCandidateContexts: {},
    reservationPolicy: "notify_only",
    seatObservationMode: "balanced",
    focusedObservationIntervalSeconds: 25,
    nextCheckAt: null,
    ...overrides,
  };
}

function activeWatch(overrides: Partial<ActiveWatch> = {}): ActiveWatch {
  return { ...mapActiveWatch(mappedWatch(), "authenticated"), ...overrides };
}

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

  it.each([
    ["pending", false, false, null, "예매 시도 중 · 22:00:00"],
    ["payment_required", false, false, null, "좌석 임시 확보 · 결제 필요 · 22:00:00"],
    ["not_available", true, false, "new_availability_episode", "예매 시도 · 좌석 확보 실패 · 감시 계속 · 22:00:00 · 매진 후 좌석이 다시 열리면 자동 예매"],
    ["auth_required", true, false, "provider_account_reverified", "예매 시도 · 철도 계정 재확인 필요 · 22:00:00"],
    ["provider_blocked", false, false, null, "예매 시도 · 운영사 제한 · 자동 재확인 대기 · 22:00:00"],
    ["unknown", false, true, null, "예매 시도 결과 확인 필요 · 22:00:00 · 공식 예매 내역을 확인해 주세요"],
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
      status: "seat_found",
      statusLabel: "좌석 발견",
      reservationPolicy: "reserve_once_before_payment",
      latestReservationAttempt: {
        outcome: "payment_required",
        startedAt: "2026-08-08T13:00:00Z",
        finishedAt: "2026-08-08T13:01:00Z",
        retryable: true,
        manualCheckRequired: false,
        retryCondition: "new_availability_episode",
        paymentHoldEndedAt: "2026-08-08T13:02:00Z",
      },
    }), false);
    const blocked = presentActiveWatchRow(mapActiveWatch(mappedWatch({
      status: "auth_required",
      statusLabel: "로그인 필요",
    }), "provider_blocked"), false);
    const reserving = presentActiveWatchRow(activeWatch({ status: "reserving" }), false);

    expect(endedHold.statusLabel).toBe("이전 결제 보류 종료 · 매진 후 재발견 대기");
    expect(endedHold.reservationAttemptLabel).toBe(
      "결제 보류 종료 확인 · 감시 계속 · 22:02:00 · 매진 후 좌석이 다시 열리면 자동 예매",
    );
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
  });
});
