import type { ToastProgressStep } from "../../shared/ui/AppToast";
import type { AppNotificationInput } from "./notificationCenter";
import type {
  ReservationProgressStageName,
  SeatAvailabilityLostTransition,
  WatchActionTransition,
} from "./watchSnapshots";
import { formatWatchIdentity, formatWatchSchedule } from "./watchJourney";

export type ReservationResultOutcome = "failed" | "not_available" | "unknown";

export interface ReservationRecoveryResult {
  outcome: ReservationResultOutcome;
  retryable: boolean;
  manualCheckRequired: boolean;
  retryCondition: "new_availability_episode" | "provider_account_reverified" | null;
}

type StepTiming = Pick<
  ToastProgressStep,
  "occurredAt" | "durationMs" | "durationPrefix" | "showNoticeDuration"
>;

const completed = (label: string, timing: StepTiming = {}): ToastProgressStep => (
  { label, state: "completed", ...timing }
);
const active = (label: string, timing: StepTiming = {}): ToastProgressStep => (
  { label, state: "active", ...timing }
);
const pending = (label: string, timing: StepTiming = {}): ToastProgressStep => (
  { label, state: "pending", ...timing }
);
const failed = (label: string, timing: StepTiming = {}): ToastProgressStep => (
  { label, state: "failed", ...timing }
);

function stageTimes(transition: WatchActionTransition | SeatAvailabilityLostTransition) {
  const detectedAt = transition.detectedAt;
  const startedAt = transition.startedAt;
  const attemptedAt = transition.finishedAt ?? transition.revisionAt;
  const detectedInstant = detectedAt === undefined ? Number.NaN : Date.parse(detectedAt);
  const startedInstant = startedAt === undefined ? Number.NaN : Date.parse(startedAt);
  const queueDurationMs = Number.isFinite(detectedInstant) && Number.isFinite(startedInstant)
    ? Math.max(0, startedInstant - detectedInstant)
    : undefined;
  return {
    detected: detectedAt === undefined ? {} : { occurredAt: detectedAt },
    started: startedAt === undefined
      ? {}
      : {
          occurredAt: startedAt,
          ...(queueDurationMs === undefined ? {} : { durationMs: queueDurationMs }),
          durationPrefix: "감지 후" as const,
        },
    attempted: attemptedAt === undefined
      ? {}
      : {
          occurredAt: attemptedAt,
          showNoticeDuration: true,
          durationPrefix: "이전 단계 후" as const,
        },
    current: transition.revisionAt === undefined ? {} : { occurredAt: transition.revisionAt },
  } satisfies Record<string, StepTiming>;
}

function journeyFields(transition: WatchActionTransition | SeatAvailabilityLostTransition) {
  return {
    meta: formatWatchIdentity(transition),
    description: formatWatchSchedule(transition),
  };
}

function completedAttemptDurationMs(
  transition: WatchActionTransition | SeatAvailabilityLostTransition,
): number | null {
  if ("status" in transition && transition.status === "reserving") return null;
  if (transition.startedAt === undefined || transition.finishedAt === undefined) return null;
  const startedAt = Date.parse(transition.startedAt);
  const finishedAt = Date.parse(transition.finishedAt);
  if (!Number.isFinite(startedAt) || !Number.isFinite(finishedAt) || finishedAt < startedAt) {
    return null;
  }
  return finishedAt - startedAt;
}

type ReservationTerminalStage =
  | "payment_required"
  | "auth_required"
  | "not_available"
  | "manual_check"
  | "failed";

const progressStageLabels: Record<ReservationProgressStageName, string> = {
  authenticated_session_ready: "로그인 세션 확인",
  target_rechecked: "검색 결과·열차 재확인",
  seat_selected: "좌석 선택",
  reservation_requested: "예약 요청",
};

function knownReservingProgressSteps(
  transition: WatchActionTransition,
): ToastProgressStep[] | null {
  if (!transition.reservationProgress?.length) return null;
  const times = stageTimes(transition);
  let previousAt = transition.startedAt;
  const steps: ToastProgressStep[] = [
    ...(transition.detectedAt === undefined ? [] : [completed("좌석 발견", times.detected)]),
    completed("자동 예매 요청 시작", times.started),
  ];
  for (const progress of transition.reservationProgress) {
    const currentInstant = Date.parse(progress.occurredAt);
    const previousInstant = previousAt === undefined ? Number.NaN : Date.parse(previousAt);
    steps.push(completed(progressStageLabels[progress.stage], {
      occurredAt: progress.occurredAt,
      ...(Number.isFinite(currentInstant)
        && Number.isFinite(previousInstant)
        && currentInstant >= previousInstant
        ? {
            durationMs: currentInstant - previousInstant,
            durationPrefix: "이전 단계 후" as const,
          }
        : {}),
    }));
    previousAt = progress.occurredAt;
  }
  steps.push(active("철도사 응답·공식 결과 대기"));
  return steps;
}

function detailedResultSteps(
  transition: WatchActionTransition,
  terminal: ReservationTerminalStage,
): ToastProgressStep[] | null {
  if (!transition.reservationProgress?.length) return null;
  const times = stageTimes(transition);
  const progressByStage = new Map(
    transition.reservationProgress.map((item) => [item.stage, item.occurredAt]),
  );
  const hasSeatSelection = progressByStage.has("seat_selected");
  let previousAt = transition.startedAt;
  const steps: ToastProgressStep[] = [
    completed("좌석 발견", times.detected),
    completed("자동 예매 요청 시작", times.started),
  ];
  const appendProgress = (
    stage: ReservationProgressStageName,
    label: string,
    state: "completed" | "failed" = "completed",
  ) => {
    const occurredAt = progressByStage.get(stage);
    if (occurredAt === undefined) return;
    const currentInstant = Date.parse(occurredAt);
    const previousInstant = previousAt === undefined ? Number.NaN : Date.parse(previousAt);
    const timing: StepTiming = {
      occurredAt,
      ...(Number.isFinite(currentInstant)
        && Number.isFinite(previousInstant)
        && currentInstant >= previousInstant
        ? {
            durationMs: currentInstant - previousInstant,
            durationPrefix: "이전 단계 후" as const,
          }
        : {}),
    };
    steps.push(state === "failed" ? failed(label, timing) : completed(label, timing));
    previousAt = occurredAt;
  };
  appendProgress("authenticated_session_ready", "로그인 세션 확인");
  appendProgress(
    "target_rechecked",
    "검색 결과·열차 재확인",
    terminal === "not_available" && !hasSeatSelection ? "failed" : "completed",
  );
  appendProgress("seat_selected", "좌석 선택");
  appendProgress("reservation_requested", "예약 요청");

  const resultAt = transition.finishedAt ?? transition.revisionAt;
  const resultInstant = resultAt === undefined ? Number.NaN : Date.parse(resultAt);
  const previousInstant = previousAt === undefined ? Number.NaN : Date.parse(previousAt);
  const resultTiming: StepTiming = resultAt === undefined
    ? {}
    : {
        occurredAt: resultAt,
        ...(Number.isFinite(resultInstant)
          && Number.isFinite(previousInstant)
          && resultInstant >= previousInstant
          ? {
              durationMs: resultInstant - previousInstant,
              durationPrefix: "이전 단계 후" as const,
            }
          : {}),
      };
  if (terminal === "auth_required" && !progressByStage.has("authenticated_session_ready")) {
    steps.push(failed("로그인 세션 확인", times.current));
  } else if (terminal === "payment_required" || terminal === "not_available") {
    steps.push(completed("공식 결과 확인", resultTiming));
  } else {
    steps.push(failed("공식 결과 확인", resultTiming));
  }
  return steps;
}

function lifecycleFields(
  transition: WatchActionTransition | SeatAvailabilityLostTransition,
): Pick<
  AppNotificationInput,
  | "subjectKey"
  | "revisionKey"
  | "revisionAt"
  | "occurredAt"
  | "startedAt"
  | "durationMs"
> {
  return {
    subjectKey: `watch:${transition.id}`,
    revisionKey: `watch:${transition.id}:${transition.revision ?? (
      "status" in transition ? transition.status : "seat_found"
    )}`,
    revisionAt: transition.revisionAt ?? null,
    occurredAt: transition.revisionAt ?? null,
    startedAt: transition.startedAt ?? (
      "status" in transition && transition.status === "reserving"
        ? transition.revisionAt ?? null
        : null
    ),
    durationMs: completedAttemptDurationMs(transition),
  };
}

export function buildSeatFoundToast(
  transition: SeatAvailabilityLostTransition,
): AppNotificationInput {
  return {
    ...lifecycleFields(transition),
    kind: "seat_found",
    key: `reservation:${transition.id}`,
    tone: "success",
    title: "좌석을 찾았습니다",
    ...journeyFields(transition),
    autoCloseMs: null,
  };
}

export function buildWatchActionToast(transition: WatchActionTransition): AppNotificationInput {
  const times = stageTimes(transition);
  const base = {
    key: `reservation:${transition.id}`,
    ...lifecycleFields(transition),
    ...journeyFields(transition),
  };
  switch (transition.status) {
    case "reserving": {
      const knownProgress = knownReservingProgressSteps(transition);
      return {
        ...base,
        kind: "reserving" as const,
        tone: "info",
        title: "예매를 진행하고 있습니다",
        description: knownProgress === null
          ? `${base.description} · 세부 단계는 철도사 결과 수신 후 표시됩니다.`
          : `${base.description} · 확인된 세부 단계를 실시간 반영하고 있습니다.`,
        steps: knownProgress ?? [
          ...(transition.detectedAt === undefined
            ? []
            : [completed("좌석 발견", times.detected)]),
          active("자동 예매 요청 시작", times.started),
          pending("철도사 응답·공식 결과 대기"),
        ],
      };
    }
    case "payment_required":
      return {
        ...base,
        kind: "payment_required" as const,
        sortAt: transition.paymentDeadline ?? null,
        tone: "success",
        title: "결제 직전까지 예매되었습니다",
        description: `${base.description} · 공식 플랫폼에서 결제해 주세요.`,
        autoCloseMs: null,
        steps: [
          ...(detailedResultSteps(transition, "payment_required") ?? [
            completed("좌석 발견", times.detected),
            completed("자동 예매 요청 시작", times.started),
            completed("예매 요청 완료", times.attempted),
          ]),
          active("공식 결제 필요", times.current),
        ],
      };
    case "payment_hold_ended": {
      const monitoringResumed = transition.automaticReservationRetry === true;
      const deadlineElapsed = transition.paymentHoldEndReason === "confirmed_payment_deadline_elapsed";
      return {
        ...base,
        kind: "recovery" as const,
        tone: "warning",
        title: deadlineElapsed
          ? "결제기한 안에 결제되지 않아 예매가 취소되었습니다"
          : "공식 확인 결과 임시 예약이 종료되었습니다",
        description: monitoringResumed
          ? `${base.description} · 공식 확인에서 임시 예약 종료를 확인했습니다. 감시는 다시 시작되며, 매진 뒤 좌석이 다시 열리면 자동 예매를 시도합니다.`
          : `${base.description} · 공식 확인에서 임시 예약 종료를 확인했습니다. 이 1회 알림 작업은 종료되었습니다.`,
        steps: [
          completed("좌석 발견", times.detected),
          completed("자동 예매 요청 시작", times.started),
          completed("좌석 임시 확보", times.attempted),
          deadlineElapsed
            ? failed("결제기한 내 결제 미완료", times.current)
            : failed("임시 예약 종료", times.current),
          completed(
            deadlineElapsed ? "결제 가능 시간 종료 확인" : "임시 예약 종료 확인",
            times.current,
          ),
          completed(monitoringResumed ? "감시 재개" : "작업 종료", times.current),
        ],
      };
    }
    case "auth_required":
      return {
        ...base,
        kind: "auth_required" as const,
        tone: "warning",
        title: "로그인 확인이 필요합니다",
        description: `${base.description} · 설정에서 철도 계정을 다시 확인해 주세요. 로그인 확인 후 감시를 재개합니다.`,
        autoCloseMs: null,
        steps: [
          ...(detailedResultSteps(transition, "auth_required") ?? [
            completed("좌석 발견", times.detected),
            completed("자동 예매 요청 시작", times.started),
            failed("계정 확인", times.attempted),
          ]),
          pending("감시 재개", times.current),
        ],
      };
    case "authentication_recovered":
      return {
        ...base,
        kind: "recovery" as const,
        tone: "success",
        title: "로그인 확인이 완료되어 감시를 재개합니다",
        steps: [completed("계정 확인"), active("감시 재개")],
      };
    case "failed":
      return {
        ...base,
        kind: "recovery" as const,
        tone: "error",
        title: "예매에 실패했습니다",
        description: `${base.description} · 상태를 확인한 뒤 감시를 다시 시작해 주세요.`,
        steps: detailedResultSteps(transition, "failed") ?? [
          completed("좌석 발견", times.detected),
          completed("자동 예매 요청 시작", times.started),
          failed("예매 요청", times.attempted),
        ],
      };
    case "monitoring_resumed":
      return buildReservationRecoveryToast(transition, {
        outcome: "unknown",
        retryable: false,
        manualCheckRequired: true,
        retryCondition: null,
      });
  }
}

export function buildReservationRecoveryToast(
  transition: WatchActionTransition,
  result: ReservationRecoveryResult,
): AppNotificationInput {
  const { outcome } = result;
  const times = stageTimes(transition);
  const base = {
    key: `reservation:${transition.id}`,
    ...lifecycleFields(transition),
    tone: outcome === "failed" ? "error" as const : "warning" as const,
    ...journeyFields(transition),
  };
  if (result.manualCheckRequired || !result.retryable) {
    const monitoringResumed = transition.monitoringResumed !== false;
    return {
      ...base,
      kind: "manual_check",
      autoCloseMs: null,
      title: "예매 결과를 확인해야 합니다",
      description: monitoringResumed
        ? `${base.description} · 결과가 불명확해 자동 재예매를 보류합니다. 공식 예약 내역을 확인해 주세요. 감시는 계속됩니다.`
        : `${base.description} · 결과가 불명확해 자동 재예매를 보류합니다. 감시는 종료되었습니다. 공식 예약 내역을 확인해 주세요.`,
      steps: [
        ...(detailedResultSteps(transition, "manual_check") ?? [
          completed("좌석 발견", times.detected),
          completed("자동 예매 요청 시작", times.started),
          completed("예매 요청", times.attempted),
          failed("공식 결과 확인"),
        ]),
        active(monitoringResumed ? "감시·수동 확인" : "공식 결과 수동 확인", times.current),
      ],
    };
  }
  if (outcome === "not_available") {
    return {
      ...base,
      kind: "recovery",
      title: "좌석이 사라져 다시 감시 중입니다",
      description: `${base.description} · 예약된 좌석은 없습니다. 좌석이 다시 확인되면 예매를 다시 시도합니다.`,
      steps: [
        ...(detailedResultSteps(transition, "not_available") ?? [
          completed("좌석 발견", times.detected),
          completed("자동 예매 요청 시작", times.started),
          failed("좌석 재확인", times.attempted),
        ]),
        active("감시·재예매 대기", times.current),
      ],
    };
  }
  return {
    ...base,
    kind: "recovery",
    title: "예매에 실패해 다시 감시 중입니다",
    description: `${base.description} · 예약된 좌석은 없습니다. 좌석이 다시 확인되면 예매를 다시 시도합니다.`,
    steps: [
      ...(detailedResultSteps(transition, "failed") ?? [
        completed("좌석 발견", times.detected),
        completed("자동 예매 요청 시작", times.started),
        failed("예매 요청", times.attempted),
      ]),
      active("감시·재예매 대기", times.current),
    ],
  };
}

export function buildAvailabilityLostToast(
  transition: SeatAvailabilityLostTransition,
): AppNotificationInput {
  return {
    key: `reservation:${transition.id}`,
    ...lifecycleFields(transition),
    kind: "recovery",
    tone: "warning",
    title: "예매 가능 좌석이 사라져 다시 감시 중입니다",
    ...journeyFields(transition),
    steps: [
      completed("좌석 발견", { occurredAt: transition.detectedAt ?? null }),
      failed("좌석 재확인", { occurredAt: transition.revisionAt ?? null }),
      active("감시 재개", { occurredAt: transition.revisionAt ?? null }),
    ],
  };
}
