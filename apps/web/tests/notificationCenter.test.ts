import { describe, expect, it } from "vitest";

import {
  initialNotificationCenterState,
  notificationCenterReducer,
  pushNotifications,
  type AppNotificationInput,
} from "../src/features/app/notificationCenter";

function notice(overrides: Partial<AppNotificationInput> = {}): AppNotificationInput {
  return {
    title: "좌석을 찾았습니다",
    subjectKey: "watch:one",
    revisionKey: "watch:one:seat:1",
    kind: "seat_found",
    ...overrides,
  };
}

describe("notification center lifecycle", () => {
  it("replaces an earlier stage for the same watch and ignores a repeated revision", () => {
    const found = pushNotifications(initialNotificationCenterState, [notice()]);
    const reserving = pushNotifications(found, [notice({
      title: "예매를 진행하고 있습니다",
      revisionKey: "watch:one:reserving:1",
      revisionAt: "2026-08-03T12:09:45Z",
      occurredAt: "2026-08-03T12:09:45Z",
      startedAt: "2026-08-03T12:09:45Z",
      kind: "reserving",
    })]);
    const completed = pushNotifications(reserving, [notice({
      title: "좌석이 사라져 다시 감시 중입니다",
      revisionKey: "watch:one:result:1",
      revisionAt: "2026-08-03T12:09:48.250Z",
      occurredAt: "2026-08-03T12:09:48.250Z",
      kind: "recovery",
    })]);
    const duplicate = pushNotifications(completed, [notice({
      title: "중복 결과",
      revisionKey: "watch:one:result:1",
      kind: "recovery",
    })]);

    expect(reserving.notices).toHaveLength(1);
    expect(reserving.notices[0]).toMatchObject({
      title: "예매를 진행하고 있습니다",
      kind: "reserving",
      persistence: "sticky",
      autoCloseMs: null,
    });
    expect(completed.notices[0]).toMatchObject({
      startedAt: "2026-08-03T12:09:45Z",
      durationMs: 3_250,
    });
    expect(duplicate.notices[0]?.title).toBe("좌석이 사라져 다시 감시 중입니다");
    expect(duplicate.sequence).toBe(completed.sequence);
  });

  it("does not let an older recovery event replace a newer actionable seat event", () => {
    const found = pushNotifications(initialNotificationCenterState, [notice({
      revisionAt: "2026-08-03T01:10:00+09:00",
    })]);
    const staleRecovery = pushNotifications(found, [notice({
      title: "과거 감시 복귀",
      revisionKey: "watch:one:recovery:old",
      revisionAt: "2026-08-03T01:09:00+09:00",
      kind: "recovery",
    })]);

    expect(staleRecovery.notices[0]).toMatchObject({
      title: "좌석을 찾았습니다",
      kind: "seat_found",
    });
    expect(staleRecovery.sequence).toBe(found.sequence);
  });

  it("orders action-required notices before progress and sorts payments by deadline", () => {
    const state = pushNotifications(initialNotificationCenterState, [
      notice({ subjectKey: "watch:progress", revisionKey: "progress", kind: "reserving" }),
      notice({
        subjectKey: "watch:later",
        revisionKey: "later",
        kind: "payment_required",
        sortAt: "2026-08-03T12:00:00+09:00",
      }),
      notice({
        subjectKey: "watch:earlier",
        revisionKey: "earlier",
        kind: "payment_required",
        sortAt: "2026-08-03T11:00:00+09:00",
      }),
      notice({ subjectKey: "watch:auth", revisionKey: "auth", kind: "auth_required" }),
    ]);

    expect(state.notices.map((item) => item.subjectKey)).toEqual([
      "watch:earlier",
      "watch:later",
      "watch:auth",
      "watch:progress",
    ]);
    expect(state.announcementMode).toBe("assertive");
  });

  it("bulk-dismisses timed information while retaining sticky action notices", () => {
    const state = pushNotifications(initialNotificationCenterState, [
      notice({ subjectKey: "watch:seat", revisionKey: "seat", kind: "seat_found" }),
      notice({ subjectKey: "watch:progress", revisionKey: "progress", kind: "reserving" }),
      notice({ subjectKey: "generic:one", revisionKey: "generic", kind: "generic" }),
    ]);

    const dismissed = notificationCenterReducer(state, { type: "dismiss_timed" });

    expect(dismissed.notices.map((item) => item.kind)).toEqual(["seat_found", "reserving"]);
  });

  it("keeps an active reservation visible until a terminal revision replaces it", () => {
    const reserving = pushNotifications(initialNotificationCenterState, [notice({
      title: "예매를 진행하고 있습니다",
      revisionKey: "watch:one:reserving:long",
      revisionAt: "2026-08-03T12:09:45Z",
      kind: "reserving",
      autoCloseMs: 1_000,
    })]);

    const afterTimedDismiss = notificationCenterReducer(reserving, { type: "dismiss_timed" });
    expect(afterTimedDismiss.notices).toHaveLength(1);
    expect(afterTimedDismiss.notices[0]).toMatchObject({
      kind: "reserving",
      persistence: "sticky",
      autoCloseMs: null,
    });

    const completed = pushNotifications(afterTimedDismiss, [notice({
      title: "좌석이 사라져 다시 감시 중입니다",
      revisionKey: "watch:one:result:long",
      revisionAt: "2026-08-03T12:11:00Z",
      kind: "recovery",
    })]);
    expect(completed.notices).toHaveLength(1);
    expect(completed.notices[0]).toMatchObject({
      title: "좌석이 사라져 다시 감시 중입니다",
      kind: "recovery",
      persistence: "timed",
    });
  });

  it("replaces a sticky payment action with terminal cancellation steps", () => {
    const payment = pushNotifications(initialNotificationCenterState, [notice({
      title: "결제 직전까지 예매되었습니다",
      revisionKey: "watch:one:payment",
      revisionAt: "2026-08-03T12:10:00Z",
      kind: "payment_required",
      steps: [{ label: "공식 결제 필요", state: "active" }],
    })]);
    const cancelled = pushNotifications(payment, [notice({
      title: "결제기한 안에 결제되지 않아 예매가 취소되었습니다",
      revisionKey: "watch:one:hold-ended",
      revisionAt: "2026-08-03T12:20:00Z",
      kind: "recovery",
      steps: [
        { label: "결제기한 내 결제 미완료", state: "failed" },
        { label: "예매 취소 확인", state: "completed" },
        { label: "감시 재개", state: "completed" },
      ],
    })]);

    expect(cancelled.notices).toHaveLength(1);
    expect(cancelled.notices[0]).toMatchObject({
      title: "결제기한 안에 결제되지 않아 예매가 취소되었습니다",
      kind: "recovery",
      persistence: "timed",
    });
    expect(cancelled.notices[0]?.steps?.some((step) => (
      step.state === "active" || step.state === "pending"
    ))).toBe(false);
  });
});
