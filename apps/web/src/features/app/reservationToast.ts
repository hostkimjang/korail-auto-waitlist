import type { ToastProgressStep } from "../../shared/ui/AppToast";
import {
  formatReservedSeats,
  type ReservationConfirmationDiagnosticCode,
  type ReservationConfirmationOutcome,
  type ReservationResultReasonCode,
} from "../../domain/reservationAttempt";
import type { AppNotificationInput } from "./notificationCenter";
import { reservationConfirmationDiagnosticDescriptions } from "../../shared/lib/reservationConfirmationDiagnostic";
import type {
  ReservationRecoveryResult,
  ReservationProgressStageName,
  SeatAvailabilityLostTransition,
  WatchActionTransition,
} from "./watchSnapshots";
import { formatWatchIdentity, formatWatchSchedule } from "./watchJourney";

export type { ReservationRecoveryResult, ReservationResultOutcome } from "./watchSnapshots";

interface ReservationEvidence {
  resultReasonCode?: ReservationResultReasonCode | null;
  confirmationOutcome?: ReservationConfirmationOutcome | null;
  confirmationDiagnosticCode?: ReservationConfirmationDiagnosticCode | null;
  confirmationObservedAt?: string | null;
  reconciliationAttemptCount?: number;
  nextReconcileAt?: string | null;
}

const resultReasonDescriptions: Record<ReservationResultReasonCode, string> = {
  reservation_pending: "철도사 예매 요청이 끝나지 않아 결과를 기다리고 있습니다.",
  payment_hold_created: "철도사 응답에서 결제가 필요한 임시 예약을 확인했습니다.",
  target_not_available: "예매 시점에 대상 열차를 찾지 못했습니다.",
  target_ambiguous: "검색 결과에서 대상 열차를 하나로 구분하지 못했습니다.",
  seat_not_available: "예매 시점에 선택 가능한 좌석이 없었습니다.",
  reservation_control_unavailable: "철도사 예매 화면의 예약 기능을 사용할 수 없었습니다.",
  seat_selection_lost: "예약 화면에서 선택한 객실 등급이 예약 요청까지 유지되지 않았습니다.",
  delay_consent_required: "철도사 지연 안내창에서 운행 지연 동의가 필요합니다.",
  existing_reservation_action_required: "철도사 기존 예약 안내창에서 진행할 예약을 선택해야 합니다.",
  provider_notice_action_required: "철도사 안내창에서 사용자 확인이 필요합니다.",
  authentication_required: "철도사 로그인이 만료되었거나 추가 인증이 필요합니다.",
  provider_blocked: "운영사 요청 제한으로 예매 처리를 계속할 수 없었습니다.",
  provider_unavailable: "철도사 예매 처리 중 연결 또는 응답 확인에 실패했습니다.",
  provider_response_invalid: "철도사 응답 형식을 확인할 수 없어 결과를 신뢰하지 않았습니다.",
  reservation_request_result_unknown: "예약 요청 처리 중 전달 여부 또는 철도사 결과를 확정하지 못했습니다.",
  reservation_failed: "철도사 예매 요청이 완료되지 않았습니다.",
};

const confirmationDescriptions: Record<ReservationConfirmationOutcome, string> = {
  confirmed_payment_required: "공식 예약 내역에서 결제가 필요한 임시 예약을 확인했습니다.",
  confirmed_paid: "공식 예약 내역에서 결제 완료를 확인했습니다.",
  not_found: "공식 예약 내역에서 대상 예약을 찾지 못했습니다.",
  auth_required: "공식 예약 내역을 확인하려면 로그인이 필요합니다.",
  provider_blocked: "운영사 제한으로 공식 예약 내역을 확인하지 못했습니다.",
  inconclusive: "공식 예약 내역 확인으로 결과를 확정하지 못했습니다.",
};

function reconciliationTimeLabel(value: string | null | undefined): string | null {
  if (!value || !Number.isFinite(Date.parse(value))) return null;
  return new Intl.DateTimeFormat("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "Asia/Seoul",
  }).format(new Date(value));
}

function evidenceDescription(evidence: ReservationEvidence): string {
  const confirmationDescription = evidence.confirmationOutcome === "inconclusive"
    ? reservationConfirmationDiagnosticDescriptions[
        evidence.confirmationDiagnosticCode ?? "unspecified"
      ]
    : evidence.confirmationOutcome
      ? confirmationDescriptions[evidence.confirmationOutcome]
      : null;
  const details = [
    evidence.resultReasonCode ? resultReasonDescriptions[evidence.resultReasonCode] : null,
    confirmationDescription,
  ];
  const count = evidence.reconciliationAttemptCount ?? 0;
  const nextAt = reconciliationTimeLabel(evidence.nextReconcileAt);
  if (count > 0) details.push(`공식 내역 자동 재확인 ${count}/6회 수행.`);
  if (nextAt !== null) details.push(`다음 자동 재확인은 ${nextAt} 예정입니다.`);
  return details.filter((detail): detail is string => detail !== null).join(" ");
}

function appendEvidence(description: string, evidence: ReservationEvidence): string {
  const detail = evidenceDescription(evidence);
  return detail ? `${description} · ${detail}` : description;
}

function manualCheckTitle(result: ReservationRecoveryResult): string {
  switch (result.resultReasonCode) {
    case "delay_consent_required":
      return "운행 지연 동의가 필요합니다";
    case "existing_reservation_action_required":
      return "기존 예약 안내를 확인해야 합니다";
    case "provider_notice_action_required":
      return "철도사 안내창 확인이 필요합니다";
    case "target_ambiguous":
      return "대상 열차를 구분하지 못했습니다";
    case "reservation_control_unavailable":
      return "철도사 예매 기능을 사용할 수 없습니다";
    case "provider_unavailable":
      return "철도사 연결 문제로 예매 결과를 확인해야 합니다";
    case "provider_response_invalid":
    case "reservation_request_result_unknown":
      return "예매 요청 결과를 확인해야 합니다";
    default:
      return result.nextReconcileAt
        ? "공식 예매 결과를 다시 확인 중입니다"
        : "예매 결과를 확인해야 합니다";
  }
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
  | "payment_completed"
  | "auth_required"
  | "not_available"
  | "manual_check"
  | "failed";

const progressStageLabels: Record<ReservationProgressStageName, string> = {
  authenticated_session_ready: "로그인 세션 확인",
  target_rechecked: "검색 결과·열차 재확인",
  seat_selected: "객실 등급 선택",
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
  appendProgress("seat_selected", "객실 등급 선택");
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
  } else if (terminal === "payment_completed") {
    steps.push(completed("좌석 임시 확보", resultTiming));
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
  const reservedSeatLabel = formatReservedSeats(transition.reservedSeats ?? []);
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
        ...(reservedSeatLabel === null
          ? {}
          : { meta: `${base.meta} · 예약 좌석 ${reservedSeatLabel}` }),
        kind: "payment_required" as const,
        sortAt: transition.paymentDeadline ?? null,
        tone: "success",
        title: "결제 직전까지 예매되었습니다",
        description: appendEvidence(
          `${base.description}${reservedSeatLabel === null ? "" : ` · 예약 좌석 ${reservedSeatLabel}`} · 공식 플랫폼에서 결제해 주세요.`,
          transition,
        ),
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
    case "payment_completed":
      return {
        ...base,
        kind: "recovery" as const,
        tone: "success",
        title: "결제가 완료되었습니다",
        description: appendEvidence(
          `${base.description} · 공식 예약 내역에서 결제 완료를 확인했습니다. 결제 안내를 종료합니다.`,
          transition,
        ),
        steps: [
          ...(detailedResultSteps(transition, "payment_completed") ?? [
            completed("좌석 임시 확보", times.attempted),
          ]),
          completed("공식 결제 완료 확인", times.current),
        ],
      };
    case "payment_hold_ended": {
      const monitoringResumed = transition.automaticReservationRetry === true;
      const deadlineElapsed = transition.paymentHoldEndReason === "confirmed_payment_deadline_elapsed";
      const holdEvidence = deadlineElapsed
        ? "공식 확인에서 결제 가능 기한이 지난 것을 확인했습니다."
        : "공식 내역에서 대상 임시 예약을 더 이상 찾지 못했습니다.";
      return {
        ...base,
        kind: "recovery" as const,
        tone: "warning",
        title: deadlineElapsed
          ? "결제 가능 기한이 지났습니다"
          : "공식 내역에서 대상 임시 예약을 더 이상 찾지 못했습니다",
        description: monitoringResumed
          ? `${base.description} · ${holdEvidence} 감시는 다시 시작되며, 매진 뒤 좌석이 다시 열리면 자동 예매를 시도합니다.`
          : `${base.description} · ${holdEvidence} 이 1회 알림 작업은 종료되었습니다.`,
        steps: [
          completed("좌석 발견", times.detected),
          completed("자동 예매 요청 시작", times.started),
          completed("좌석 임시 확보", times.attempted),
          deadlineElapsed
            ? failed("결제 가능 시간 종료", times.current)
            : failed("대상 임시 예약 목록 부재", times.current),
          completed(
            deadlineElapsed ? "결제 가능 시간 종료 확인" : "공식 내역 목록 부재 확인",
            times.current,
          ),
          completed(monitoringResumed ? "감시 재개" : "작업 종료", times.current),
        ],
      };
    }
    case "auth_required":
      if (
        transition.resultReasonCode === "provider_blocked"
        || transition.confirmationOutcome === "provider_blocked"
      ) {
        return {
          ...base,
          kind: "auth_required" as const,
          tone: "warning",
          title: "운영사 요청 제한으로 확인이 필요합니다",
          description: appendEvidence(
            `${base.description} · 자동 예매와 공식 내역 확인을 중단했습니다. 운영사 이용 상태를 확인한 뒤 감시를 재개해 주세요.`,
            transition,
          ),
          autoCloseMs: null,
          steps: [
            ...(detailedResultSteps(transition, "auth_required") ?? [
              completed("좌석 발견", times.detected),
              completed("자동 예매 요청 시작", times.started),
              failed("운영사 요청 제한 확인", times.attempted),
            ]),
            pending("운영사 상태 확인 후 감시 재개", times.current),
          ],
        };
      }
      return {
        ...base,
        kind: "auth_required" as const,
        tone: "warning",
        title: "로그인 확인이 필요합니다",
        description: appendEvidence(
          `${base.description} · 설정에서 철도 계정을 다시 확인해 주세요. 로그인 확인 후 감시를 재개합니다.`,
          transition,
        ),
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
        description: appendEvidence(
          `${base.description} · 상태를 확인한 뒤 감시를 다시 시작해 주세요.`,
          transition,
        ),
        steps: detailedResultSteps(transition, "failed") ?? [
          completed("좌석 발견", times.detected),
          completed("자동 예매 요청 시작", times.started),
          failed("예매 요청", times.attempted),
        ],
      };
    case "monitoring_resumed":
      return buildReservationRecoveryToast(transition, transition.reservationResult ?? {
        outcome: "unknown",
        retryable: false,
        manualCheckRequired: true,
        retryCondition: null,
        resultReasonCode: transition.resultReasonCode ?? null,
        confirmationOutcome: transition.confirmationOutcome ?? null,
        confirmationDiagnosticCode: transition.confirmationDiagnosticCode ?? null,
        confirmationObservedAt: transition.confirmationObservedAt ?? null,
        reconciliationAttemptCount: transition.reconciliationAttemptCount ?? 0,
        nextReconcileAt: transition.nextReconcileAt ?? null,
      });
  }
}

export function buildReservationRecoveryToast(
  transition: WatchActionTransition,
  result: ReservationRecoveryResult,
): AppNotificationInput {
  const { outcome } = result;
  const times = stageTimes(transition);
  const reservedSeatLabel = formatReservedSeats(transition.reservedSeats ?? []);
  const journey = journeyFields(transition);
  const base = {
    key: `reservation:${transition.id}`,
    ...lifecycleFields(transition),
    tone: outcome === "failed" ? "error" as const : "warning" as const,
    ...journey,
    ...(reservedSeatLabel === null
      ? {}
      : { meta: `${journey.meta} · 예약 좌석 ${reservedSeatLabel}` }),
  };
  if (result.manualCheckRequired || !result.retryable) {
    const monitoringResumed = transition.monitoringResumed !== false;
    const automaticRecheckPending = Boolean(result.nextReconcileAt);
    const summary = monitoringResumed
      ? `${base.description} · 자동 재예매를 보류합니다. 공식 예약 내역을 확인해 주세요. 감시는 계속됩니다.`
      : `${base.description} · 자동 재예매를 보류합니다. 감시는 종료되었습니다. 공식 예약 내역을 확인해 주세요.`;
    return {
      ...base,
      kind: "manual_check",
      autoCloseMs: null,
      title: manualCheckTitle(result),
      description: appendEvidence(summary, result),
      steps: [
        ...(detailedResultSteps(transition, "manual_check") ?? [
          completed("좌석 발견", times.detected),
          completed("자동 예매 요청 시작", times.started),
          completed("예매 요청", times.attempted),
          failed("공식 결과 확인"),
        ]),
        active(
          automaticRecheckPending
            ? "공식 결과 자동 재확인 대기"
            : monitoringResumed ? "감시·수동 확인" : "공식 결과 수동 확인",
          times.current,
        ),
      ],
    };
  }
  if (outcome === "not_available") {
    const monitoringResumed = transition.monitoringResumed !== false;
    return {
      ...base,
      kind: "recovery",
      title: monitoringResumed
        ? "좌석이 사라져 다시 감시 중입니다"
        : "좌석을 확보하지 못해 작업이 종료되었습니다",
      description: monitoringResumed
        ? appendEvidence(
            `${base.description} · 예약된 좌석은 없습니다. 좌석이 다시 확인되면 예매를 다시 시도합니다.`,
            result,
          )
        : appendEvidence(`${base.description} · 예약된 좌석은 없습니다. 감시는 종료되었습니다.`, result),
      steps: [
        ...(detailedResultSteps(transition, "not_available") ?? [
          completed("좌석 발견", times.detected),
          completed("자동 예매 요청 시작", times.started),
          failed("좌석 재확인", times.attempted),
        ]),
        monitoringResumed
          ? active("감시·재예매 대기", times.current)
          : completed("작업 종료", times.current),
      ],
    };
  }
  return {
    ...base,
    kind: "recovery",
    title: "예매에 실패해 다시 감시 중입니다",
    description: appendEvidence(
      `${base.description} · 예약된 좌석은 없습니다. 좌석이 다시 확인되면 예매를 다시 시도합니다.`,
      result,
    ),
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
