import { describe, expect, it } from "vitest";

import {
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
    ...overrides,
  };
}

describe("reservation recovery toast", () => {
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
        label: "예매 시작",
        state: "completed",
        occurredAt: "2026-08-03T12:09:45Z",
        durationMs: 1_000,
        durationPrefix: "대기",
      },
      {
        label: "좌석 재확인",
        state: "failed",
        occurredAt: "2026-08-03T12:09:48Z",
        durationPrefix: "처리",
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
