import { describe, expect, it } from "vitest";

import type { LatestReservationAttempt } from "../src/domain/reservationAttempt";
import type { WatchStatus } from "../src/domain/watch";
import {
  detectSeatAvailabilityLostTransitions,
  detectSeatFoundTransitions,
  detectWatchActionTransitions,
  hydrateCurrentWatchActionTransitions,
  reconcileWatchSnapshots,
  type WatchSnapshot,
} from "../src/features/app/watchSnapshots";
import type { WatchLifecycleSnapshot } from "../src/features/app/watchLifecycleSnapshot";
import { buildWatchActionToast } from "../src/features/app/reservationToast";
import {
  createInitialNotificationCenterState,
  initialNotificationCenterState,
  notificationCenterReducer,
  pushNotifications,
} from "../src/features/app/notificationCenter";

function attempt(
  values: Partial<LatestReservationAttempt>,
): LatestReservationAttempt {
  return {
    outcome: "pending",
    startedAt: "2026-08-03T12:09:45Z",
    finishedAt: null,
    retryable: false,
    manualCheckRequired: false,
    retryCondition: null,
    paymentHoldEndedAt: null,
    progressStages: [],
    ...values,
  };
}

function watch(id: string, status: WatchStatus): WatchLifecycleSnapshot {
  return {
    id,
    status,
    provider: "KORAIL",
    train: `KTX ${id}`,
    route: "서울 → 부산",
    seatClassLabel: "일반실",
    date: "8월 3일 (월)",
    departure: "14:35",
    arrival: "15:39",
    latestReservationAttempt: null,
    latestReservationAttemptCandidateId: null,
    paymentDeadline: null,
    reservationCandidateContexts: {},
    reservationPolicy: "notify_only",
    seatFoundObservation: status === "seat_found"
      ? {
        kind: "official_provider",
        source: "korail-pydoll-reservation",
        observedAt: "2026-07-31T12:00:00+09:00",
        observedLabel: "최근 확인 12:00",
      }
      : null,
    updatedAt: null,
  };
}

describe("watch snapshot reconciliation", () => {
  it("keeps every public transition detector compatible with legacy snapshots", () => {
    const watching: WatchSnapshot = {
      id: "legacy-transition",
      status: "watching",
      provider: "KORAIL",
      train: "KTX 085",
    };
    const seatFound: WatchSnapshot = {
      ...watching,
      status: "seat_found",
      seatFoundObservation: { observedAt: "2026-08-01T03:45:00Z" },
    };
    const reserving: WatchSnapshot = {
      ...watching,
      status: "reserving",
      updated_at: "2026-08-01T03:46:00Z",
    };

    expect(detectSeatFoundTransitions([watching], [seatFound]))
      .toMatchObject([{ id: "legacy-transition" }]);
    expect(detectSeatAvailabilityLostTransitions([seatFound], [watching]))
      .toMatchObject([{ id: "legacy-transition" }]);
    expect(detectWatchActionTransitions([seatFound], [reserving]))
      .toMatchObject([{ id: "legacy-transition", status: "reserving" }]);
  });

  it("ignores initial seat-found rows and reports only later watching-family transitions", () => {
    const initial = [watch("old", "seat_found"), watch("one", "watching"), watch("two", "scheduled")];
    expect(detectSeatFoundTransitions([], initial)).toEqual([]);

    const next = [watch("old", "seat_found"), watch("one", "seat_found"), watch("two", "seat_found")];
    expect(detectSeatFoundTransitions(initial, next).map((item) => item.id)).toEqual(["one", "two"]);
    expect(detectSeatFoundTransitions(next, next)).toEqual([]);
  });

  it("does not announce an automatic seat discovery before a reservation attempt is claimed", () => {
    const previous = [watch("auto", "watching")];
    const next: WatchLifecycleSnapshot[] = [{
      ...watch("auto", "seat_found"),
      reservationPolicy: "reserve_once_before_payment",
    }];

    expect(detectSeatFoundTransitions(previous, next)).toEqual([]);
  });

  it("preserves unchanged watch and array identities", () => {
    const previous = [watch("one", "watching"), watch("two", "watching")];
    const same = previous.map((item) => ({ ...item }));
    const unchanged = reconcileWatchSnapshots(previous, same);
    expect(unchanged).toBe(previous);

    const changed = reconcileWatchSnapshots(previous, [watch("one", "watching"), watch("two", "seat_found")]);
    expect(changed).not.toBe(previous);
    expect(changed[0]).toBe(previous[0]);
    expect(changed[1]).not.toBe(previous[1]);
  });

  it("reports an availability loss only on the actionable-to-unavailable edge", () => {
    const available: WatchLifecycleSnapshot = {
      ...watch("one", "seat_found"),
      seatFoundObservation: {
        kind: "official_provider",
        source: "korail-pydoll-reservation",
        observedAt: "2026-07-31T12:00:00+09:00",
        observedLabel: "최근 확인 12:00",
      },
    };
    const unavailable = watch("one", "watching");

    expect(detectSeatAvailabilityLostTransitions([], [available])).toEqual([]);
    expect(detectSeatAvailabilityLostTransitions([available], [unavailable])).toMatchObject([{
      id: "one",
      train: "KTX one",
      seatClassLabel: "일반실",
      date: "8월 3일 (월)",
      departure: "14:35",
      arrival: "15:39",
    }]);
    expect(detectSeatAvailabilityLostTransitions(
      [available],
      [{ ...unavailable, status: "reserving" }],
    )).toEqual([]);
    expect(detectSeatAvailabilityLostTransitions([unavailable], [unavailable])).toEqual([]);
  });

  it("reports only later reservation and action-required status edges", () => {
    const previous = [watch("reserve", "seat_found"), watch("pay", "reserving"), watch("auth", "watching")];
    const next = [watch("reserve", "reserving"), watch("pay", "payment_required"), watch("auth", "auth_required")];

    expect(detectWatchActionTransitions([], next)).toEqual([]);
    expect(detectWatchActionTransitions(previous, next).map((item) => item.status))
      .toEqual(["reserving", "payment_required", "auth_required"]);
    expect(detectWatchActionTransitions(next, next)).toEqual([]);
  });

  it.each([
    ["auth_required", "로그인 확인이 필요합니다"],
    ["provider_blocked", "운영사 요청 제한으로 확인이 필요합니다"],
  ] as const)("preserves UNKNOWN reconciliation %s on the canonical auth edge", (
    confirmationOutcome,
    title,
  ) => {
    const previous = watch(`reconciled-${confirmationOutcome}`, "watching");
    const candidateId = "candidate-auth";
    const next: WatchLifecycleSnapshot = {
      ...previous,
      status: "auth_required",
      latestReservationAttemptCandidateId: candidateId,
      reservationCandidateContexts: {
        [candidateId]: {
          train: "KTX 223",
          seatClassLabel: "특실",
          date: "8월 15일 (토)",
          departure: "22:08",
          arrival: "23:07",
        },
      },
      latestReservationAttempt: attempt({
        outcome: "unknown",
        resultReasonCode: "reservation_request_result_unknown",
        finishedAt: "2026-08-15T12:10:00Z",
        manualCheckRequired: true,
        confirmationOutcome,
        confirmationObservedAt: "2026-08-15T12:12:00Z",
        reconciliationAttemptCount: 2,
        nextReconcileAt: null,
      }),
      updatedAt: "2026-08-15T12:12:01Z",
    };

    const transitions = detectWatchActionTransitions([previous], [next]);
    const transition = transitions[0];
    if (transition === undefined) throw new Error("canonical authentication edge was not created");

    expect(transitions).toMatchObject([{
      status: "auth_required",
      train: "KTX 223",
      seatClassLabel: "특실",
      confirmationOutcome,
      confirmationObservedAt: "2026-08-15T12:12:00Z",
      revisionAt: "2026-08-15T12:12:01Z",
    }]);
    expect(buildWatchActionToast(transition).title).toBe(title);
  });

  it("hydrates only current actionable reservation states and keeps seat-found as baseline", () => {
    const reserving = {
      ...watch("reserve", "reserving"),
      latestReservationAttempt: attempt({ startedAt: "2026-08-03T12:09:45Z" }),
    };
    const payment = {
      ...watch("pay", "payment_required"),
      latestReservationAttempt: attempt({
        startedAt: "2026-08-03T12:09:45Z",
        finishedAt: "2026-08-03T12:09:48Z",
      }),
    };
    const auth = {
      ...watch("auth", "auth_required"),
      updatedAt: "2026-08-03T12:10:00Z",
    };

    expect(hydrateCurrentWatchActionTransitions([
      watch("watching", "watching"),
      watch("found", "seat_found"),
      reserving,
      payment,
      auth,
    ])).toMatchObject([
      { id: "reserve", status: "reserving", revision: "reserving:2026-08-03T12:09:45Z" },
      { id: "pay", status: "payment_required", revision: "payment_required:2026-08-03T12:09:48Z" },
      { id: "auth", status: "auth_required", revision: "auth_required:2026-08-03T12:10:00Z" },
    ]);
  });

  it("uses the actual lifecycle stage timestamp instead of an older seat observation", () => {
    const previous = [watch("reserve-time", "seat_found")];
    const reserving = {
      ...watch("reserve-time", "reserving"),
      updatedAt: "2026-08-03T12:09:46Z",
      latestReservationAttempt: attempt({
        startedAt: "2026-08-03T12:09:45Z",
        finishedAt: "2026-08-03T12:09:48Z",
      }),
    };

    expect(detectWatchActionTransitions(previous, [reserving]))
      .toMatchObject([{ status: "reserving", revisionAt: "2026-08-03T12:09:45Z" }]);
  });

  it("does not infer a reservation result from a generic return to monitoring", () => {
    const previous = [watch("resume", "reserving")];
    const next = [watch("resume", "watching")];

    expect(detectWatchActionTransitions(previous, next)).toEqual([]);
  });

  it("projects a durable not-available result even when the watch status stays watching", () => {
    const previousWatching = watch("canonical-result", "watching");
    const previousReserving: WatchLifecycleSnapshot = {
      ...watch("canonical-result", "reserving"),
      latestReservationAttempt: attempt({
        startedAt: "2026-08-03T12:09:45Z",
      }),
    };
    const recovered: WatchLifecycleSnapshot = {
      ...watch("canonical-result", "watching"),
      latestReservationAttempt: attempt({
        outcome: "not_available",
        startedAt: "2026-08-03T12:09:45Z",
        finishedAt: "2026-08-03T12:09:48Z",
        retryable: true,
        retryCondition: "new_availability_episode",
        progressStages: [{
          stage: "target_rechecked",
          occurredAt: "2026-08-03T12:09:47Z",
        }],
      }),
      latestReservationAttemptCandidateId: "candidate-result",
      reservationCandidateContexts: {
        "candidate-result": {
          train: "KTX 053",
          seatClassLabel: "일반실",
          date: "8월 13일 (목)",
          departure: "17:58",
          arrival: "18:57",
        },
      },
    };

    for (const previous of [previousWatching, previousReserving]) {
      const transitions = detectWatchActionTransitions([previous], [recovered]);
      expect(transitions).toMatchObject([{
        status: "monitoring_resumed",
        revisionAt: "2026-08-03T12:09:48Z",
        revision: expect.stringContaining("candidate-result"),
        train: "KTX 053",
        monitoringResumed: true,
        reservationResult: {
          outcome: "not_available",
          retryable: true,
          manualCheckRequired: false,
          retryCondition: "new_availability_episode",
        },
      }]);
      const progressState = pushNotifications(initialNotificationCenterState, [{
        subjectKey: `watch:${recovered.id}`,
        revisionKey: `watch:${recovered.id}:progress-before-result-loss`,
        revisionAt: "2026-08-03T12:09:47Z",
        kind: "reserving",
        title: "예매를 진행하고 있습니다",
      }]);
      const recoveryNotice = transitions[0];
      if (recoveryNotice === undefined) throw new Error("canonical recovery transition missing");
      const recoveredState = pushNotifications(progressState, [
        buildWatchActionToast(recoveryNotice),
      ]);
      expect(recoveredState.notices).toMatchObject([{
        subjectKey: `watch:${recovered.id}`,
        kind: "recovery",
        persistence: "timed",
        title: "좌석이 사라져 다시 감시 중입니다",
      }]);
    }
    expect(detectWatchActionTransitions([recovered], [recovered])).toEqual([]);

    const expired = { ...recovered, status: "expired" as const };
    const expiredTransition = detectWatchActionTransitions([previousReserving], [expired])[0];
    if (expiredTransition === undefined) throw new Error("expired recovery transition missing");
    const expiredToast = buildWatchActionToast(expiredTransition);
    expect(expiredToast).toMatchObject({
      kind: "recovery",
      title: "좌석을 확보하지 못해 작업이 종료되었습니다",
    });
    expect(expiredToast.steps).toContainEqual(expect.objectContaining({
      label: "작업 종료",
      state: "completed",
    }));
  });

  it("replaces and rehydrates reserving only with a durable unknown manual-check result", () => {
    const previous = [watch("resume", "reserving")];
    const recovered: WatchLifecycleSnapshot = {
      ...watch("resume", "watching"),
      latestReservationAttempt: attempt({
        outcome: "unknown",
        startedAt: "2026-08-03T12:09:45Z",
        finishedAt: "2026-08-03T12:10:15Z",
        manualCheckRequired: true,
        progressStages: [{
          stage: "authenticated_session_ready",
          occurredAt: "2026-08-03T12:09:46Z",
        }],
      }),
      latestReservationAttemptCandidateId: "candidate-two",
      reservationCandidateContexts: {
        "candidate-two": {
          train: "KTX 326",
          seatClassLabel: "일반실",
          date: "8월 12일 (수)",
          departure: "12:15",
          arrival: "13:08",
        },
      },
    };

    expect(detectWatchActionTransitions(previous, [recovered])).toMatchObject([{
      status: "monitoring_resumed",
      revisionAt: "2026-08-03T12:10:15Z",
      train: "KTX 326",
      reservationProgress: [{ stage: "authenticated_session_ready" }],
    }]);
    expect(hydrateCurrentWatchActionTransitions([recovered])).toMatchObject([{
      status: "monitoring_resumed",
      revision: expect.stringContaining("candidate-two"),
      monitoringResumed: true,
      reservationResult: {
        outcome: "unknown",
        manualCheckRequired: true,
      },
    }]);

    const expired = { ...recovered, status: "expired" as const };
    expect(hydrateCurrentWatchActionTransitions([expired])).toMatchObject([{
      status: "monitoring_resumed",
      monitoringResumed: false,
    }]);
  });

  it("revises a durable manual-check notice for each newer official confirmation", () => {
    const first: WatchLifecycleSnapshot = {
      ...watch("reconcile-progress", "watching"),
      latestReservationAttemptCandidateId: "candidate-two",
      latestReservationAttempt: attempt({
        outcome: "unknown",
        resultReasonCode: "reservation_request_result_unknown",
        startedAt: "2026-08-03T12:09:45Z",
        finishedAt: "2026-08-03T12:10:15Z",
        manualCheckRequired: true,
        confirmationOutcome: "inconclusive",
        confirmationObservedAt: "2026-08-03T12:11:00Z",
        reconciliationAttemptCount: 1,
        nextReconcileAt: "2026-08-03T12:12:00Z",
      }),
    };
    const second: WatchLifecycleSnapshot = {
      ...first,
      latestReservationAttempt: attempt({
        outcome: "unknown",
        resultReasonCode: "reservation_request_result_unknown",
        startedAt: "2026-08-03T12:09:45Z",
        finishedAt: "2026-08-03T12:10:15Z",
        manualCheckRequired: true,
        confirmationOutcome: "inconclusive",
        confirmationObservedAt: "2026-08-03T12:12:00Z",
        reconciliationAttemptCount: 2,
        nextReconcileAt: "2026-08-03T12:14:00Z",
      }),
    };

    const firstTransition = hydrateCurrentWatchActionTransitions([first])[0];
    const secondTransition = detectWatchActionTransitions([first], [second])[0];
    if (firstTransition === undefined || secondTransition === undefined) {
      throw new Error("reconciliation progress transitions were not created");
    }
    expect(secondTransition).toMatchObject({
      status: "monitoring_resumed",
      revisionAt: "2026-08-03T12:12:00Z",
      reservationResult: {
        confirmationOutcome: "inconclusive",
        reconciliationAttemptCount: 2,
        nextReconcileAt: "2026-08-03T12:14:00Z",
      },
    });
    expect(secondTransition.revision).not.toBe(firstTransition.revision);

    const firstState = pushNotifications(initialNotificationCenterState, [
      buildWatchActionToast(firstTransition),
    ]);
    const secondState = pushNotifications(firstState, [buildWatchActionToast(secondTransition)]);
    expect(secondState.notices[0]).toMatchObject({
      revisionAt: "2026-08-03T12:12:00Z",
      title: "예매 요청 결과를 확인해야 합니다",
    });
    expect(secondState.notices[0]?.description).toContain("공식 내역 자동 재확인 2/6회 수행");
  });

  it("replaces a REST manual-check snapshot with confirmed absence and revises its consumed fence", () => {
    const candidateId = "candidate-confirmed-absent";
    const manual: WatchLifecycleSnapshot = {
      ...watch("confirmed-absent-rest", "watching"),
      latestReservationAttemptCandidateId: candidateId,
      reservationCandidateContexts: {
        [candidateId]: {
          train: "KTX 240",
          seatClassLabel: "일반실",
          date: "8월 19일 (수)",
          departure: "12:28",
          arrival: "13:36",
        },
      },
      latestReservationAttempt: attempt({
        outcome: "unknown",
        resultReasonCode: "reservation_request_result_unknown",
        startedAt: "2026-08-18T03:20:00Z",
        finishedAt: "2026-08-18T03:20:08Z",
        manualCheckRequired: true,
        confirmationOutcome: "inconclusive",
        confirmationObservedAt: "2026-08-18T03:20:10Z",
        reconciliationAttemptCount: 1,
        nextReconcileAt: "2026-08-18T03:20:20Z",
      }),
    };
    const confirmedAbsentAttempt = attempt({
      outcome: "unknown",
      resultReasonCode: "reservation_request_result_unknown",
      startedAt: "2026-08-18T03:20:00Z",
      finishedAt: "2026-08-18T03:20:08Z",
      manualCheckRequired: false,
      confirmationOutcome: "not_found",
      confirmationObservedAt: "2026-08-18T03:20:20Z",
      reconciliationAttemptCount: 2,
      reconciliationResolution: "confirmed_absent",
      nextReconcileAt: null,
    });
    const confirmedAbsent: WatchLifecycleSnapshot = {
      ...manual,
      latestReservationAttempt: confirmedAbsentAttempt,
    };
    const consumed: WatchLifecycleSnapshot = {
      ...confirmedAbsent,
      latestReservationAttempt: attempt({
        ...confirmedAbsentAttempt,
        automaticReservationRetryFenceReason: "confirmed_absent_recovery_consumed",
      }),
    };

    const manualTransition = hydrateCurrentWatchActionTransitions([manual])[0];
    const sourceTransition = detectWatchActionTransitions([manual], [confirmedAbsent])[0];
    const consumedTransition = detectWatchActionTransitions([confirmedAbsent], [consumed])[0];
    if (
      manualTransition === undefined
      || sourceTransition === undefined
      || consumedTransition === undefined
    ) throw new Error("confirmed-absence REST transitions were not created");

    expect(sourceTransition.reservationResult).toMatchObject({
      outcome: "unknown",
      manualCheckRequired: false,
      reconciliationResolution: "confirmed_absent",
      automaticReservationRetryFenceReason: null,
    });
    expect(consumedTransition.reservationResult).toMatchObject({
      reconciliationResolution: "confirmed_absent",
      automaticReservationRetryFenceReason: "confirmed_absent_recovery_consumed",
    });
    expect(consumedTransition.revision).not.toBe(sourceTransition.revision);

    const manualState = pushNotifications(initialNotificationCenterState, [
      buildWatchActionToast(manualTransition),
    ]);
    expect(manualState.notices[0]).toMatchObject({
      kind: "manual_check",
      persistence: "sticky",
    });
    const sourceState = pushNotifications(manualState, [buildWatchActionToast(sourceTransition)]);
    expect(sourceState.notices).toHaveLength(1);
    expect(sourceState.notices[0]).toMatchObject({
      kind: "recovery",
      title: "공식 예약 없음이 확인되어 감시 중입니다",
    });
    expect(sourceState.notices[0]?.description).not.toContain("공식 예약 내역을 확인해 주세요");

    const consumedState = pushNotifications(sourceState, [
      buildWatchActionToast(consumedTransition),
    ]);
    expect(consumedState.notices).toHaveLength(1);
    expect(consumedState.notices[0]).toMatchObject({
      kind: "recovery",
      title: "자동 복구 1회 사용을 마쳐 감시 중입니다",
    });
    expect(consumedState.notices[0]?.description).toContain("추가 자동 예매는 차단됩니다");
  });

  it("revises the same inconclusive outcome when only its diagnostic becomes more specific", () => {
    const first: WatchLifecycleSnapshot = {
      ...watch("diagnostic-revision", "watching"),
      latestReservationAttemptCandidateId: "candidate-diagnostic",
      latestReservationAttempt: attempt({
        outcome: "unknown",
        resultReasonCode: "reservation_request_result_unknown",
        startedAt: "2026-08-03T12:09:45Z",
        finishedAt: "2026-08-03T12:10:15Z",
        manualCheckRequired: true,
        confirmationOutcome: "inconclusive",
        confirmationDiagnosticCode: "unspecified",
        confirmationObservedAt: "2026-08-03T12:11:00Z",
        reconciliationAttemptCount: 1,
        nextReconcileAt: null,
      }),
    };
    const second: WatchLifecycleSnapshot = {
      ...first,
      latestReservationAttempt: attempt({
        outcome: "unknown",
        resultReasonCode: "reservation_request_result_unknown",
        startedAt: "2026-08-03T12:09:45Z",
        finishedAt: "2026-08-03T12:10:15Z",
        manualCheckRequired: true,
        confirmationOutcome: "inconclusive",
        confirmationDiagnosticCode: "official_record_ambiguous",
        confirmationObservedAt: "2026-08-03T12:11:00Z",
        reconciliationAttemptCount: 1,
        nextReconcileAt: null,
      }),
    };

    const previous = hydrateCurrentWatchActionTransitions([first])[0];
    const revised = detectWatchActionTransitions([first], [second])[0];
    if (previous === undefined || revised === undefined) {
      throw new Error("diagnostic-only reconciliation transition was not created");
    }

    expect(revised.revision).not.toBe(previous.revision);
    expect(revised.reservationResult).toMatchObject({
      confirmationOutcome: "inconclusive",
      confirmationDiagnosticCode: "official_record_ambiguous",
    });
    expect(buildWatchActionToast(revised).description).toContain(
      "공식 내역에서 이번 예매 시도와 정확히 일치하는 항목을 하나로 구분하지 못했습니다.",
    );

    const initialNoticeState = pushNotifications(initialNotificationCenterState, [
      buildWatchActionToast(previous),
    ]);
    const revisedNoticeState = pushNotifications(initialNoticeState, [
      buildWatchActionToast(revised),
    ]);
    expect(revisedNoticeState.notices).toHaveLength(1);
    expect(revisedNoticeState.notices[0]?.revisionKey)
      .toBe(`watch:${second.id}:${revised.revision}`);
    expect(revisedNoticeState.notices[0]?.description).toContain(
      "공식 내역에서 이번 예매 시도와 정확히 일치하는 항목을 하나로 구분하지 못했습니다.",
    );
    expect(revisedNoticeState.notices[0]?.description).not.toContain(
      "공식 예약 내역 확인으로 결과를 확정하지 못했습니다.",
    );
  });

  it("replaces a same-attempt manual check with a later reconciled payment action", () => {
    const finishedAt = "2026-08-03T12:10:15Z";
    const reconciledAt = "2026-08-03T12:12:30Z";
    const manualCheck: WatchLifecycleSnapshot = {
      ...watch("reconciled-payment", "watching"),
      updatedAt: finishedAt,
      latestReservationAttemptCandidateId: "candidate-payment",
      latestReservationAttempt: attempt({
        outcome: "unknown",
        startedAt: "2026-08-03T12:09:45Z",
        finishedAt,
        manualCheckRequired: true,
      }),
      reservationCandidateContexts: {
        "candidate-payment": {
          train: "KTX 326",
          seatClassLabel: "일반실",
          date: "8월 12일 (수)",
          departure: "12:15",
          arrival: "13:08",
        },
      },
    };
    const paymentRequired: WatchLifecycleSnapshot = {
      ...manualCheck,
      status: "payment_required",
      updatedAt: reconciledAt,
      paymentDeadline: "2026-08-03T12:30:00Z",
      latestReservationAttempt: attempt({
        outcome: "payment_required",
        startedAt: "2026-08-03T12:09:45Z",
        finishedAt,
      }),
    };
    const manualTransition = hydrateCurrentWatchActionTransitions([manualCheck])[0];
    const paymentTransition = detectWatchActionTransitions([manualCheck], [paymentRequired])[0];
    if (manualTransition === undefined || paymentTransition === undefined) {
      throw new Error("reconciliation transitions were not created");
    }
    expect(paymentTransition).toMatchObject({
      status: "payment_required",
      revision: `payment_required:${reconciledAt}`,
      revisionAt: reconciledAt,
      finishedAt,
    });

    const manualState = pushNotifications(initialNotificationCenterState, [
      buildWatchActionToast(manualTransition),
    ]);
    expect(manualState.notices[0]?.kind).toBe("manual_check");
    const activeReplacement = pushNotifications(manualState, [
      buildWatchActionToast(paymentTransition),
    ]);
    expect(activeReplacement.notices[0]).toMatchObject({
      kind: "payment_required",
      revisionAt: reconciledAt,
    });

    const manualNoticeId = manualState.notices[0]?.id;
    if (manualNoticeId === undefined) throw new Error("manual-check notice was not created");
    const dismissed = notificationCenterReducer(manualState, {
      type: "dismiss",
      id: manualNoticeId,
    });
    const remounted = createInitialNotificationCenterState(dismissed.dismissalLedger);
    const hydratedPayment = hydrateCurrentWatchActionTransitions([paymentRequired])[0];
    if (hydratedPayment === undefined) throw new Error("payment hydration transition was not created");
    const reopened = pushNotifications(remounted, [buildWatchActionToast(hydratedPayment)]);
    expect(reopened.notices[0]).toMatchObject({
      kind: "payment_required",
      revisionAt: reconciledAt,
    });
  });

  it("reconciles durable progress and manual-check evidence without a status edge", () => {
    const reserving = {
      ...watch("same-status-progress", "reserving"),
      latestReservationAttempt: attempt({
        startedAt: "2026-08-03T12:09:45Z",
      }),
    };
    const progressed = {
      ...reserving,
      latestReservationAttempt: attempt({
        startedAt: "2026-08-03T12:09:45Z",
        progressStages: [{
          stage: "target_rechecked",
          occurredAt: "2026-08-03T12:09:47Z",
        }],
      }),
    };

    expect(detectWatchActionTransitions([reserving], [progressed])).toMatchObject([{
      status: "reserving",
      revision: "reserving:2026-08-03T12:09:47Z",
      reservationProgress: [{ stage: "target_rechecked" }],
    }]);

    const pendingExpired = {
      ...watch("same-status-manual", "expired"),
      latestReservationAttempt: attempt({
        startedAt: "2026-08-03T12:09:45Z",
      }),
    };
    const recoveredExpired = {
      ...pendingExpired,
      latestReservationAttempt: attempt({
        outcome: "unknown",
        startedAt: "2026-08-03T12:09:45Z",
        finishedAt: "2026-08-03T12:10:15Z",
        manualCheckRequired: true,
      }),
    };
    expect(detectWatchActionTransitions([pendingExpired], [recoveredExpired])).toMatchObject([{
      status: "monitoring_resumed",
      monitoringResumed: false,
    }]);
  });

  it("reports authentication recovery when an auth-required watch resumes monitoring", () => {
    const previous = [watch("resume", "auth_required")];
    const recovered = {
      ...watch("resume", "watching"),
      updatedAt: "2026-08-03T12:12:00Z",
      latestReservationAttempt: attempt({
        startedAt: "2026-08-03T11:00:00Z",
        finishedAt: "2026-08-03T11:00:03Z",
      }),
    };

    expect(detectWatchActionTransitions(previous, [watch("resume", "scheduled")]))
      .toMatchObject([{ id: "resume", status: "authentication_recovered" }]);
    expect(detectWatchActionTransitions(previous, [recovered]))
      .toMatchObject([{
        id: "resume",
        status: "authentication_recovered",
        revisionAt: "2026-08-03T12:12:00Z",
      }]);
  });

  it("reports payment-hold end only with the server-confirmed hold-ended marker", () => {
    const previous = [watch("payment", "payment_required")];
    const resumedWithoutEvidence = [watch("payment", "watching")];
    const holdEndedAt = "2026-08-03T12:20:01Z";
    const confirmedHoldEnded: WatchLifecycleSnapshot = {
      ...watch("payment", "watching"),
      latestReservationAttempt: attempt({
        outcome: "payment_required",
        startedAt: "2026-08-03T12:09:45Z",
        finishedAt: "2026-08-03T12:09:48Z",
        paymentHoldEndedAt: holdEndedAt,
        paymentHoldEndReason: "confirmed_payment_deadline_elapsed",
      }),
    };
    const resumed = [confirmedHoldEnded];
    const oneOffExpired: WatchLifecycleSnapshot[] = [{
      ...confirmedHoldEnded,
      status: "expired",
    }];

    expect(detectWatchActionTransitions(previous, resumedWithoutEvidence)).toEqual([]);
    expect(detectWatchActionTransitions(previous, resumed)).toMatchObject([{
      id: "payment",
      status: "payment_hold_ended",
      automaticReservationRetry: true,
      paymentHoldEndReason: "confirmed_payment_deadline_elapsed",
      revisionAt: holdEndedAt,
    }]);
    expect(detectWatchActionTransitions(previous, oneOffExpired)).toMatchObject([{
      id: "payment",
      status: "payment_hold_ended",
      automaticReservationRetry: false,
      revisionAt: holdEndedAt,
    }]);
  });

  it("turns a canonical paid completion edge into a terminal notification transition", () => {
    const previous = [watch("paid", "payment_required")];
    const completedAt = "2026-08-03T12:21:01Z";
    const completed: WatchLifecycleSnapshot = {
      ...watch("paid", "completed"),
      updatedAt: completedAt,
    };

    expect(detectWatchActionTransitions(previous, [completed])).toMatchObject([{
      id: "paid",
      status: "payment_completed",
      revision: `payment_completed:${completedAt}`,
      revisionAt: completedAt,
    }]);
    expect(detectWatchActionTransitions([completed], [completed])).toEqual([]);
  });

  it("recovers an UNKNOWN paid completion only from exact confirmed-paid canonical evidence", () => {
    const previous = [watch("unknown-paid", "watching")];
    const completedAt = "2026-08-03T12:21:01Z";
    const confirmedPaid: WatchLifecycleSnapshot = {
      ...watch("unknown-paid", "completed"),
      latestReservationAttemptCandidateId: "candidate",
      reservationCandidateContexts: {
        candidate: { train: "KTX 9248", seatClassLabel: "일반실" },
      },
      latestReservationAttempt: attempt({
        outcome: "unknown",
        finishedAt: "2026-08-03T12:09:48Z",
        manualCheckRequired: true,
        confirmationOutcome: "confirmed_paid",
        confirmationObservedAt: completedAt,
      }),
      updatedAt: completedAt,
    };

    expect(detectWatchActionTransitions(previous, [confirmedPaid])).toMatchObject([{
      id: "unknown-paid",
      status: "payment_completed",
      train: "KTX 9248",
      revision: `payment_completed:${completedAt}`,
    }]);
    expect(detectWatchActionTransitions(previous, [{
      ...confirmedPaid,
      latestReservationAttempt: attempt({
        outcome: "unknown",
        finishedAt: "2026-08-03T12:09:48Z",
        manualCheckRequired: true,
        confirmationOutcome: "inconclusive",
        confirmationObservedAt: completedAt,
      }),
    }])).toEqual([]);
    expect(detectWatchActionTransitions(previous, [{
      ...confirmedPaid,
      latestReservationAttemptCandidateId: "missing-candidate",
    }])).toEqual([]);
  });
});
