import {
  formatTrainIdentity,
  type WatchObservationExecutionState,
  type WatchProvider,
  type WatchSeatClass,
  type WatchStatus,
} from "../../domain/watch";
import type {
  ReservationAttemptCandidateContext,
  WatchReadModel,
} from "../../api/watchProjection";
import {
  hasConfirmedAbsentReservationEvidence,
  validatedManualRearmReason,
  type LatestReservationAttempt,
  type ManualRearmReason,
  type ReservationConfirmationOutcome,
  type ReservationResultReasonCode,
} from "../../domain/reservationAttempt";
import type { ReservationPolicy } from "../../domain/reservationPolicy";
import type { OperationalCandidateMeta } from "../../domain/watchOperational";
import { reservationConfirmationDiagnosticDescriptions } from "../../shared/lib/reservationConfirmationDiagnostic";

export type RailAccountAuthStatus =
  | "not_checked"
  | "authenticated"
  | "auth_required"
  | "provider_blocked"
  | "failed";

export type SeatFoundObservationContext =
  | {
    kind: "official_provider";
    observedAt: string | null;
    observedLabel: string;
  }
  | {
    kind: "mock";
    observedAt: string | null;
    observedLabel: string;
  };

export interface ActiveWatch {
  id: string;
  provider: WatchProvider;
  route: string;
  train: string;
  trainType?: string | null;
  date: string;
  departure: string;
  arrival: string;
  status: WatchStatus;
  statusLabel: string;
  accountAuthStatus?: RailAccountAuthStatus | null;
  seatClass: WatchSeatClass;
  seatClassLabel: string;
  seatEvidenceLabel: string;
  registrationEvidenceLabel?: string | null;
  activityLabel?: string;
  lastCheckedAt?: string | null;
  lastCheckedLabel?: string;
  origin?: string;
  destination?: string;
  travelDate?: string;
  officialBookingUrl?: string | null;
  seatFoundObservation?: SeatFoundObservationContext | null;
  reservationPolicy?: ReservationPolicy;
  nextCheckAt?: string | null;
  observationExecutionState?: WatchObservationExecutionState;
  operational?: OperationalCandidateMeta | null;
  latestReservationAttempt?: LatestReservationAttempt | null;
  latestReservationAttemptCandidateId?: string | null;
  latestReservationAttemptContext?: ReservationAttemptCandidateContext | null;
}

export interface ActiveWatchRowPresentation {
  authSummary: string;
  automaticReservationEnabled: boolean;
  canPause: boolean;
  canManualRearmReservation: boolean;
  manualRearmReason: ManualRearmReason | null;
  canRenderSeatFoundAction: boolean;
  hasAuthenticatedAccount: boolean;
  isAuthRequired: boolean;
  isProviderReverificationPending: boolean;
  nextCheckLabel: string | null;
  nextReservationPolicy: ReservationPolicy;
  policyAccountLinkLabel: string;
  policySwitchDisabled: boolean;
  policySwitchTitle: string | undefined;
  reservationAttemptLabel: string | null;
  seatEvidenceLabel: string;
  shouldShowPolicyAccountLink: boolean;
  shouldShowRegistrationEvidence: boolean;
  statusLabel: string;
}

const pausableWatchStatuses: ReadonlySet<WatchStatus> = new Set([
  "scheduled",
  "watching",
  "official_waitlist",
  "seat_found",
  "reserving",
  "cooldown",
  "auth_required",
]);

const reservationPolicyEditableStatuses: ReadonlySet<WatchStatus> = new Set([
  "draft",
  "scheduled",
  "watching",
  "official_waitlist",
  "seat_found",
  "paused",
  "cooldown",
  "auth_required",
]);

function koreaTimeLabel(value: string): string {
  return new Intl.DateTimeFormat("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
    timeZone: "Asia/Seoul",
  }).format(new Date(value));
}

const attemptReasonLabels: Record<ReservationResultReasonCode, string> = {
  reservation_pending: "철도사 응답 대기",
  payment_hold_created: "임시 예약 생성",
  target_not_available: "대상 열차 없음",
  target_ambiguous: "대상 열차 구분 불가",
  seat_not_available: "선택 가능 좌석 없음",
  reservation_control_unavailable: "예매 기능 사용 불가",
  seat_selection_lost: "객실 등급 선택 상태 유지 실패",
  delay_consent_required: "운행 지연 동의 필요",
  existing_reservation_action_required: "기존 예약 안내 확인 필요",
  provider_notice_action_required: "철도사 안내창 확인 필요",
  authentication_required: "철도사 로그인 필요",
  provider_blocked: "운영사 요청 제한",
  provider_unavailable: "철도사 연결·응답 확인 불가",
  provider_response_invalid: "철도사 응답 확인 불가",
  reservation_request_result_unknown: "예약 요청 결과 불명확",
  reservation_failed: "예약 요청 실패",
};

const confirmationLabels: Record<ReservationConfirmationOutcome, string> = {
  confirmed_payment_required: "공식 내역에서 결제 필요 확인",
  confirmed_paid: "공식 내역에서 결제 완료 확인",
  not_found: "공식 내역에서 대상 예약 없음",
  auth_required: "공식 내역 확인에 로그인 필요",
  provider_blocked: "운영사 제한으로 공식 내역 확인 불가",
  inconclusive: "공식 내역 확인 결과 불명확",
};

function reservationEvidenceLabel(attempt: LatestReservationAttempt): string {
  const confirmationLabel = attempt.confirmationOutcome === "inconclusive"
    ? reservationConfirmationDiagnosticDescriptions[
        attempt.confirmationDiagnosticCode ?? "unspecified"
      ]
    : attempt.confirmationOutcome
      ? confirmationLabels[attempt.confirmationOutcome]
      : null;
  const details = [
    attempt.resultReasonCode ? attemptReasonLabels[attempt.resultReasonCode] : null,
    confirmationLabel,
    (attempt.reconciliationAttemptCount ?? 0) > 0
      ? `공식 재확인 ${attempt.reconciliationAttemptCount ?? 0}/6회`
      : null,
    hasConfirmedAbsentReservationEvidence(attempt)
      ? "공식 재확인에서 대상 예약 없음 확정"
      : null,
    attempt.automaticReservationRetryFenceReason === "confirmed_absent_recovery_consumed"
      ? "공식 부재 확인 뒤 자동 복구 1회 사용 완료 · 추가 자동 예매 차단"
      : null,
    attempt.nextReconcileAt
      ? `다음 재확인 ${koreaTimeLabel(attempt.nextReconcileAt)}`
      : null,
  ].filter((detail): detail is string => detail !== null);
  return details.length === 0 ? "" : ` · ${details.join(" · ")}`;
}

function reservationAttemptContextLabel(context: ReservationAttemptCandidateContext): string {
  const arrival = context.arrival === null
    ? "도착 시각 미확인"
    : `${context.arrival} 도착`;
  return [
    "예매 대상",
    context.provider,
    formatTrainIdentity(context.trainType, context.train),
    context.date,
    `${context.departure} 출발`,
    arrival,
    context.seatClassLabel,
  ].join(" · ");
}

function reservationAttemptLabel(
  attempt: LatestReservationAttempt,
  context: ReservationAttemptCandidateContext | null | undefined,
): string {
  if (context === null || context === undefined) {
    return "예매 시도 대상 열차를 정확히 연결하지 못해 상세 결과를 표시하지 않습니다. 공식 예매 내역 전체를 확인해 주세요.";
  }
  const contextLabel = reservationAttemptContextLabel(context);
  if (attempt.paymentHoldEndedAt !== null) {
    const nextAction = attempt.manualRearmAvailable
      ? " · 다시 시도하려면 사용자 확인 필요"
      : " · 감시 계속";
    const holdResult = attempt.paymentHoldEndReason === "confirmed_payment_deadline_elapsed"
      ? "공식 확인에서 결제 가능 기한 종료"
      : "공식 내역에서 대상 임시 예약을 더 이상 찾지 못함";
    return `${contextLabel} · ${holdResult} · ${koreaTimeLabel(attempt.paymentHoldEndedAt)}${nextAction}`;
  }
  const occurredAt = attempt.finishedAt ?? attempt.startedAt;
  const time = koreaTimeLabel(occurredAt);
  const evidence = reservationEvidenceLabel(attempt);
  const preBookingProviderFailure = attempt.outcome === "failed"
    && !attempt.manualCheckRequired
    && attempt.resultReasonCode === "provider_unavailable"
    && !attempt.progressStages?.length
    && attempt.confirmationOutcome == null
    && attempt.confirmationObservedAt == null
    && (attempt.reconciliationAttemptCount ?? 0) === 0
    && attempt.nextReconcileAt == null;
  if (preBookingProviderFailure) {
    return `${contextLabel} · 예매 전 철도사 연결 확인 실패 · ${time}${evidence} · 확인된 예약 요청 단계 없음`;
  }
  if (attempt.outcome === "pending") return `${contextLabel} · 예매 시도 중 · ${time}${evidence}`;
  if (attempt.outcome === "payment_required" || attempt.outcome === "reserved") {
    return `${contextLabel} · 좌석 임시 확보 · 결제 필요 · ${time}${evidence}`;
  }
  if (attempt.outcome === "not_available") {
    const retryNote = attempt.retryable
      && attempt.retryCondition === "new_availability_episode"
      ? " · 매진 후 좌석이 다시 열리면 자동 예매"
      : "";
    return `${contextLabel} · 예매 시도 · 좌석 확보 실패 · 감시 계속 · ${time}${retryNote}${evidence}`;
  }
  if (attempt.outcome === "auth_required") {
    return `${contextLabel} · 예매 시도 · 철도 계정 재확인 필요 · ${time}${evidence}`;
  }
  if (attempt.outcome === "provider_blocked") {
    return `${contextLabel} · 예매 시도 · 운영사 제한 · 자동 재확인 대기 · ${time}${evidence}`;
  }
  if (attempt.outcome === "failed") {
    if (attempt.manualCheckRequired) {
      return `${contextLabel} · 예매 시도 결과 확인 필요 · ${time}${evidence} · 공식 예매 내역을 확인해 주세요`;
    }
    return `${contextLabel} · 예매 전 처리 중단 · ${time}${evidence} · 자동 재예매 미실행`;
  }
  if (
    hasConfirmedAbsentReservationEvidence(attempt)
  ) {
    const recoveryState = attempt.automaticReservationRetryFenceReason
      === "confirmed_absent_recovery_consumed"
      ? "공식 예약 없음 확인 · 자동 복구 1회 사용 완료 · 추가 자동 예매 차단 · 감시 계속"
      : "공식 예약 없음 확인 · 결과 확인 해소 · 감시 계속";
    return `${contextLabel} · ${recoveryState} · ${time}${evidence}`;
  }
  if (
    attempt.outcome === "unknown"
    && validatedManualRearmReason(attempt) === "unknown_result_unresolved"
  ) {
    return `${contextLabel} · 예약 결과 자동 확인 불가 · ${time}${evidence} · 공식 앱/홈에서 해당 열차·좌석 등급 예약 없음 확인 후 다시 시도 가능`;
  }
  if (attempt.outcome === "unknown" || attempt.manualCheckRequired) {
    return `${contextLabel} · 예매 시도 결과 확인 필요 · ${time}${evidence} · 공식 예매 내역을 확인해 주세요`;
  }
  return `${contextLabel} · 예매 시도 중단 · 운영사 상태 확인 필요 · ${time}${evidence}`;
}

type ReconciliationAuthenticationOutcome = Extract<
  ReservationConfirmationOutcome,
  "auth_required" | "provider_blocked"
>;

function reconciliationAuthenticationOutcome(
  watch: ActiveWatch,
): ReconciliationAuthenticationOutcome | null {
  const attempt = watch.latestReservationAttempt;
  if (
    watch.status !== "auth_required"
    || attempt?.outcome !== "unknown"
    || attempt.confirmationObservedAt === null
    || attempt.confirmationObservedAt === undefined
    || !Number.isFinite(Date.parse(attempt.confirmationObservedAt))
    || (attempt.reconciliationAttemptCount ?? 0) < 1
  ) return null;
  return attempt.confirmationOutcome === "auth_required"
    || attempt.confirmationOutcome === "provider_blocked"
    ? attempt.confirmationOutcome
    : null;
}

export function mapActiveWatch(
  watch: WatchReadModel,
  accountAuthStatus: RailAccountAuthStatus | null,
): ActiveWatch {
  const latestReservationAttemptContext = watch.latestReservationAttempt !== null
    && watch.latestReservationAttemptCandidateId !== null
    && watch.latestReservationAttemptCandidateId !== undefined
    && watch.latestReservationAttemptContext?.candidateId
      === watch.latestReservationAttemptCandidateId
    && watch.latestReservationAttemptContext.provider === watch.provider
    ? watch.latestReservationAttemptContext
    : null;
  return {
    id: watch.id,
    provider: watch.provider,
    route: watch.route,
    train: watch.train,
    trainType: watch.trainType ?? null,
    date: watch.date,
    departure: watch.departure,
    arrival: watch.arrival,
    status: watch.status,
    statusLabel: watch.statusLabel,
    accountAuthStatus,
    seatClass: watch.seatClass,
    seatClassLabel: watch.seatClassLabel,
    seatEvidenceLabel: watch.seatEvidenceLabel,
    registrationEvidenceLabel: watch.registrationEvidenceLabel,
    activityLabel: watch.activityLabel,
    lastCheckedAt: watch.lastCheckedAt,
    lastCheckedLabel: watch.lastCheckedLabel,
    origin: watch.origin,
    destination: watch.destination,
    travelDate: watch.travelDate,
    officialBookingUrl: watch.officialBookingUrl,
    seatFoundObservation: watch.seatFoundObservation === null
      ? null
      : {
        kind: watch.seatFoundObservation.kind,
        observedAt: watch.seatFoundObservation.observedAt,
        observedLabel: watch.seatFoundObservation.observedLabel,
      },
    reservationPolicy: watch.reservationPolicy,
    nextCheckAt: watch.nextCheckAt,
    observationExecutionState: watch.observationExecutionState,
    operational: watch.operational,
    latestReservationAttempt: watch.latestReservationAttempt,
    latestReservationAttemptCandidateId: watch.latestReservationAttemptCandidateId ?? null,
    latestReservationAttemptContext,
  };
}

export function presentActiveWatchRow(
  watch: ActiveWatch,
  isReservationPolicyUpdating: boolean,
): ActiveWatchRowPresentation {
  const seatEvidenceLabel = watch.seatEvidenceLabel
    ?? watch.activityLabel
    ?? `${watch.seatClassLabel ?? "좌석"} · 등록 근거 없음`;
  const hasAuthenticatedAccount = watch.accountAuthStatus === "authenticated";
  const isAccountStatusLoading = watch.accountAuthStatus === null || watch.accountAuthStatus === undefined;
  const reconciliationAuthOutcome = reconciliationAuthenticationOutcome(watch);
  const isProviderReverificationPending = watch.status === "auth_required"
    && (
      watch.accountAuthStatus === "provider_blocked"
      || reconciliationAuthOutcome === "provider_blocked"
    );
  const paymentHoldEnded = watch.latestReservationAttempt?.paymentHoldEndedAt !== null
    && watch.latestReservationAttempt?.paymentHoldEndedAt !== undefined;
  const hasExactAttemptContext = watch.latestReservationAttemptContext !== null
    && watch.latestReservationAttemptContext !== undefined
    && watch.latestReservationAttemptCandidateId !== null
    && watch.latestReservationAttemptCandidateId !== undefined
    && watch.latestReservationAttemptContext.candidateId
      === watch.latestReservationAttemptCandidateId
    && watch.latestReservationAttemptContext.provider === watch.provider;
  const manualRearmReason = watch.latestReservationAttempt === null
    || watch.latestReservationAttempt === undefined
    || !hasExactAttemptContext
    ? null
    : validatedManualRearmReason(watch.latestReservationAttempt);
  const unknownResultUnresolved = manualRearmReason === "unknown_result_unresolved";
  const confirmedAbsentRecoveryConsumed = watch.latestReservationAttempt
    ?.automaticReservationRetryFenceReason === "confirmed_absent_recovery_consumed";
  const automaticReservationEnabled = watch.reservationPolicy === "reserve_once_before_payment";
  const reservationPolicyEditable = reservationPolicyEditableStatuses.has(watch.status);
  const policySwitchDisabled = isReservationPolicyUpdating
    || !reservationPolicyEditable
    || (!automaticReservationEnabled && !hasAuthenticatedAccount);
  const policySwitchTitle = !reservationPolicyEditable
    ? "예약 시도가 시작된 뒤에는 실행 방식을 변경할 수 없습니다."
    : !automaticReservationEnabled && !hasAuthenticatedAccount
      ? "로그인 확인된 철도 계정이 필요합니다."
      : undefined;

  return {
    authSummary: isProviderReverificationPending
      ? "저장된 계정으로 운영사 세션을 자동 재확인 중"
      : reconciliationAuthOutcome === "auth_required"
        ? `${watch.provider} 계정 재확인 필요`
        : hasAuthenticatedAccount
          ? "계정 확인됨 · 이전 인증 상태 확인"
          : isAccountStatusLoading
            ? "철도 계정 상태를 확인하고 있습니다"
            : `${watch.provider} 계정 재확인 필요`,
    automaticReservationEnabled,
    canPause: pausableWatchStatuses.has(watch.status),
    canManualRearmReservation: watch.status === "watching"
      && automaticReservationEnabled
      && manualRearmReason !== null,
    manualRearmReason,
    canRenderSeatFoundAction: (
      watch.status === "seat_found"
        || (watch.status === "watching" && paymentHoldEnded)
    ) && watch.seatFoundObservation !== null && watch.seatFoundObservation !== undefined,
    hasAuthenticatedAccount,
    isAuthRequired: watch.status === "auth_required",
    isProviderReverificationPending,
    nextCheckLabel: watch.observationExecutionState === "in_progress"
      ? "좌석 관측 중"
      : watch.nextCheckAt
        ? `다음 좌석 관측 목표 ${koreaTimeLabel(watch.nextCheckAt)}`
        : null,
    nextReservationPolicy: automaticReservationEnabled
      ? "notify_only"
      : "reserve_once_before_payment",
    policyAccountLinkLabel: isProviderReverificationPending ? "운영사 상태 확인" : "로그인 필요",
    policySwitchDisabled,
    policySwitchTitle,
    reservationAttemptLabel: watch.latestReservationAttempt
      ? reservationAttemptLabel(
          watch.latestReservationAttempt,
          watch.latestReservationAttemptContext,
        )
      : null,
    seatEvidenceLabel,
    shouldShowPolicyAccountLink: !automaticReservationEnabled && !hasAuthenticatedAccount,
    shouldShowRegistrationEvidence: Boolean(
      watch.registrationEvidenceLabel && watch.registrationEvidenceLabel !== seatEvidenceLabel,
    ),
    statusLabel: confirmedAbsentRecoveryConsumed && watch.status === "watching"
      ? "자동 복구 1회 사용 완료 · 감시 중"
      : unknownResultUnresolved && watch.status === "watching"
      ? "예약 결과 자동 확인 불가 · 감시 중"
      : paymentHoldEnded && watch.status === "watching"
      ? watch.latestReservationAttempt?.paymentHoldEndReason === "confirmed_payment_deadline_elapsed"
        ? "결제 가능 기한 종료 확인 · 감시 중"
        : "대상 임시 예약 목록 부재 · 감시 중"
      : watch.status === "seat_found" && paymentHoldEnded
        ? watch.latestReservationAttempt?.paymentHoldEndReason === "confirmed_payment_deadline_elapsed"
          ? "결제 가능 기한 종료 확인 · 좌석 발견"
          : "대상 임시 예약 목록 부재 · 좌석 발견"
      : watch.status === "seat_found"
        ? "좌석 발견 · 감시 계속"
        : isProviderReverificationPending
          ? "운영사 제한 · 자동 재확인 대기"
          : reconciliationAuthOutcome === "auth_required"
            ? "로그인 필요"
            : watch.status === "auth_required" && hasAuthenticatedAccount
              ? "대기 확인 필요"
              : watch.status === "auth_required" && isAccountStatusLoading
                ? "계정 상태 확인 중"
                : watch.statusLabel,
  };
}

export function activeWatchRefreshLabel(lastRefreshedAt: Date | null): string {
  if (lastRefreshedAt === null) return "최근 갱신 --:--:--";
  return `최근 갱신 ${new Intl.DateTimeFormat("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(lastRefreshedAt)}`;
}
