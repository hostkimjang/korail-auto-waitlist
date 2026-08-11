import type { WatchReadModel } from "../../api/watchProjection";
import type { LatestReservationAttempt } from "../../domain/reservationAttempt";
import type { ReservationPolicy } from "../../domain/reservationPolicy";
import type {
  WatchObservationExecutionState,
  WatchProvider,
  WatchSeatClass,
  WatchStatus,
} from "../../domain/watch";
import type { OperationalCandidateMeta } from "../../domain/watchOperational";

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
}

export interface ActiveWatchRowPresentation {
  authSummary: string;
  automaticReservationEnabled: boolean;
  canPause: boolean;
  canManualRearmReservation: boolean;
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

function reservationAttemptLabel(attempt: LatestReservationAttempt): string {
  if (attempt.paymentHoldEndedAt !== null) {
    const nextAction = attempt.manualRearmAvailable
      ? " · 다시 시도하려면 사용자 확인 필요"
      : " · 감시 계속";
    return `이전 예약을 결제하지 않았습니다 · ${koreaTimeLabel(attempt.paymentHoldEndedAt)}${nextAction}`;
  }
  const occurredAt = attempt.finishedAt ?? attempt.startedAt;
  const time = koreaTimeLabel(occurredAt);
  if (attempt.outcome === "pending") return `예매 시도 중 · ${time}`;
  if (attempt.outcome === "payment_required" || attempt.outcome === "reserved") {
    return `좌석 임시 확보 · 결제 필요 · ${time}`;
  }
  if (attempt.outcome === "not_available") {
    const retryNote = attempt.retryable
      && attempt.retryCondition === "new_availability_episode"
      ? " · 매진 후 좌석이 다시 열리면 자동 예매"
      : "";
    return `예매 시도 · 좌석 확보 실패 · 감시 계속 · ${time}${retryNote}`;
  }
  if (attempt.outcome === "auth_required") {
    return `예매 시도 · 철도 계정 재확인 필요 · ${time}`;
  }
  if (attempt.outcome === "provider_blocked") {
    return `예매 시도 · 운영사 제한 · 자동 재확인 대기 · ${time}`;
  }
  if (attempt.outcome === "failed" || attempt.outcome === "unknown" || attempt.manualCheckRequired) {
    return `예매 시도 결과 확인 필요 · ${time} · 공식 예매 내역을 확인해 주세요`;
  }
  return `예매 시도 중단 · 운영사 상태 확인 필요 · ${time}`;
}

export function mapActiveWatch(
  watch: WatchReadModel,
  accountAuthStatus: RailAccountAuthStatus | null,
): ActiveWatch {
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
  const isProviderReverificationPending = watch.status === "auth_required"
    && watch.accountAuthStatus === "provider_blocked";
  const paymentHoldEnded = watch.latestReservationAttempt?.paymentHoldEndedAt !== null
    && watch.latestReservationAttempt?.paymentHoldEndedAt !== undefined;
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
      : hasAuthenticatedAccount
        ? "계정 확인됨 · 이전 인증 상태 확인"
        : isAccountStatusLoading
          ? "철도 계정 상태를 확인하고 있습니다"
          : `${watch.provider} 계정 재확인 필요`,
    automaticReservationEnabled,
    canPause: pausableWatchStatuses.has(watch.status),
    canManualRearmReservation: watch.status === "watching"
      && automaticReservationEnabled
      && paymentHoldEnded
      && watch.latestReservationAttempt?.manualRearmAvailable === true,
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
      ? reservationAttemptLabel(watch.latestReservationAttempt)
      : null,
    seatEvidenceLabel,
    shouldShowPolicyAccountLink: !automaticReservationEnabled && !hasAuthenticatedAccount,
    shouldShowRegistrationEvidence: Boolean(
      watch.registrationEvidenceLabel && watch.registrationEvidenceLabel !== seatEvidenceLabel,
    ),
    statusLabel: paymentHoldEnded && watch.status === "watching"
      ? "이전 예약 미결제 · 감시 중"
      : watch.status === "seat_found" && paymentHoldEnded
        ? "이전 예약 미결제 · 좌석 발견"
      : watch.status === "seat_found"
        ? "좌석 발견 · 감시 계속"
        : isProviderReverificationPending
          ? "운영사 제한 · 자동 재확인 대기"
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
