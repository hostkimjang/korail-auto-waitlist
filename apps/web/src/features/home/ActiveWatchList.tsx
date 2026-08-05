import { ArrowRight, ArrowsClockwise, Clock, Pause, Play, Plus, Trash } from "@phosphor-icons/react";
import type { ReactNode } from "react";

import type { ReservationPolicy } from "../../domain/reservationPolicy";
import type { LatestReservationAttempt } from "../../domain/reservationAttempt";
import type { WatchProvider, WatchSeatClass, WatchStatus } from "../../domain/watch";
import type { OperationalCandidateMeta } from "../../domain/watchOperational";
import { StatusPill } from "../../shared/ui/StatusPill";

export type { WatchStatus } from "../../domain/watch";

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
  operational?: OperationalCandidateMeta | null;
  latestReservationAttempt?: LatestReservationAttempt | null;
}

export interface WatchRowProps {
  watch: ActiveWatch;
  onPause: (watchId: string) => void | Promise<void>;
  onResume: (watchId: string) => void | Promise<void>;
  onCancel: (watchId: string) => void | Promise<void>;
  onOpenRailAccounts?: () => void;
  onChangeReservationPolicy?: (watchId: string, policy: ReservationPolicy) => void | Promise<void>;
  isReservationPolicyUpdating?: boolean;
  renderSeatFoundAction?: (watch: ActiveWatch) => ReactNode;
}

export interface ActiveWatchListProps {
  watches: ActiveWatch[];
  isRefreshing?: boolean;
  lastRefreshedAt?: Date | null;
  onCreate: () => void;
  onViewAll: () => void;
  onRefresh?: () => void;
  onPause: WatchRowProps["onPause"];
  onResume: WatchRowProps["onResume"];
  onCancel: WatchRowProps["onCancel"];
  onOpenRailAccounts?: WatchRowProps["onOpenRailAccounts"];
  onChangeReservationPolicy?: WatchRowProps["onChangeReservationPolicy"];
  reservationPolicyUpdatingIds?: ReadonlySet<string>;
  renderSeatFoundAction?: WatchRowProps["renderSeatFoundAction"];
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
    return `결제 보류 종료 확인 · 감시 계속 · ${koreaTimeLabel(attempt.paymentHoldEndedAt)} · 매진 후 좌석이 다시 열리면 자동 예매`;
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

export function WatchRow({
  watch,
  onPause,
  onResume,
  onCancel,
  onOpenRailAccounts,
  onChangeReservationPolicy,
  isReservationPolicyUpdating = false,
  renderSeatFoundAction,
}: WatchRowProps) {
  const seatEvidenceLabel = watch.seatEvidenceLabel
    ?? watch.activityLabel
    ?? `${watch.seatClassLabel ?? "좌석"} · 등록 근거 없음`;
  const seatFoundAction = watch.status === "seat_found" && watch.seatFoundObservation
    ? renderSeatFoundAction?.(watch)
    : null;
  const hasAuthenticatedAccount = watch.accountAuthStatus === "authenticated";
  const isAccountStatusLoading = watch.accountAuthStatus === null || watch.accountAuthStatus === undefined;
  const isProviderReverificationPending = watch.status === "auth_required"
    && watch.accountAuthStatus === "provider_blocked";
  const paymentHoldEnded = watch.latestReservationAttempt?.paymentHoldEndedAt !== null
    && watch.latestReservationAttempt?.paymentHoldEndedAt !== undefined;
  const statusLabel = watch.status === "seat_found" && paymentHoldEnded
    ? "이전 결제 보류 종료 · 매진 후 재발견 대기"
    : watch.status === "seat_found"
      ? "좌석 발견 · 감시 계속"
    : isProviderReverificationPending
      ? "운영사 제한 · 자동 재확인 대기"
    : watch.status === "auth_required" && hasAuthenticatedAccount
      ? "대기 확인 필요"
      : watch.status === "auth_required" && isAccountStatusLoading
        ? "계정 상태 확인 중"
      : watch.statusLabel;
  const authSummary = isProviderReverificationPending
    ? "저장된 계정으로 운영사 세션을 자동 재확인 중"
    : hasAuthenticatedAccount
    ? "계정 확인됨 · 이전 인증 상태 확인"
    : isAccountStatusLoading
      ? "철도 계정 상태를 확인하고 있습니다"
    : `${watch.provider} 계정 재확인 필요`;
  const automaticReservationEnabled = watch.reservationPolicy === "reserve_once_before_payment";
  const canEnableAutomaticReservation = hasAuthenticatedAccount;
  const reservationPolicyEditable = reservationPolicyEditableStatuses.has(watch.status);
  const policySwitchDisabled = isReservationPolicyUpdating
    || !reservationPolicyEditable
    || (!automaticReservationEnabled && !canEnableAutomaticReservation);
  const nextReservationPolicy: ReservationPolicy = automaticReservationEnabled
    ? "notify_only"
    : "reserve_once_before_payment";
  return (
    <article className={watch.status === "auth_required" ? "watch-row is-auth-required" : "watch-row"}>
      <div className="watch-provider">
        <span className={`provider-chip ${watch.provider === "SRT" ? "provider-srt" : "provider-korail"}`}>{watch.provider}</span>
        <div><strong>{watch.route}</strong><span>{watch.train} · {watch.date}</span></div>
      </div>
      <div className="watch-time"><strong>{watch.departure}</strong><ArrowRight size={18} /><strong>{watch.arrival}</strong></div>
      <div className="watch-state">
        {watch.status === "auth_required" ? (
          <div className="watch-auth-line">
            <StatusPill status={watch.status}>{statusLabel}</StatusPill>
            <span className="watch-auth-summary">{authSummary}</span>
            {onOpenRailAccounts ? (
              <button type="button" className="button button-outline compact watch-auth-action" onClick={onOpenRailAccounts}>
                {isProviderReverificationPending ? "운영사 상태" : "철도 계정"}
              </button>
            ) : null}
          </div>
        ) : <StatusPill status={watch.status}>{statusLabel}</StatusPill>}
        <span className="watch-seat-evidence">{seatEvidenceLabel}</span>
        {watch.registrationEvidenceLabel && watch.registrationEvidenceLabel !== seatEvidenceLabel
          ? <span className="watch-registration-evidence">등록 당시 {watch.registrationEvidenceLabel}</span>
          : null}
        {watch.operational ? (
          <span
            className={`watch-operational is-${watch.operational.status}${watch.operational.fresh ? " is-fresh" : ""}`}
          >
            {watch.operational.label}
          </span>
        ) : null}
        {watch.latestReservationAttempt ? (
          <span className="watch-reservation-attempt">
            {reservationAttemptLabel(watch.latestReservationAttempt)}
          </span>
        ) : null}
        <span className="watch-last-checked">{watch.lastCheckedLabel ?? "최근 확인 기록 없음"}</span>
        {watch.nextCheckAt ? (
          <span className="watch-next-check">다음 관측 예정 {koreaTimeLabel(watch.nextCheckAt)}</span>
        ) : null}
      </div>
      <div className="row-actions">
        <div className="watch-policy-control">
          <span className="watch-policy-label">
            {automaticReservationEnabled ? "좌석 재발견마다 자동 예매" : "감시만"}
          </span>
          <button
            type="button"
            role="switch"
            aria-label={`${watch.train} ${watch.seatClassLabel} 좌석 재발견마다 자동 예매 설정`}
            aria-checked={automaticReservationEnabled}
            aria-busy={isReservationPolicyUpdating}
            className={automaticReservationEnabled ? "watch-policy-switch is-on" : "watch-policy-switch"}
            disabled={policySwitchDisabled || !onChangeReservationPolicy}
            title={!reservationPolicyEditable
              ? "예약 시도가 시작된 뒤에는 실행 방식을 변경할 수 없습니다."
              : !automaticReservationEnabled && !canEnableAutomaticReservation
                ? "로그인 확인된 철도 계정이 필요합니다."
                : undefined}
            onClick={() => onChangeReservationPolicy?.(watch.id, nextReservationPolicy)}
          >
            <span aria-hidden="true" />
          </button>
          {!automaticReservationEnabled && !canEnableAutomaticReservation && onOpenRailAccounts ? (
            <button type="button" className="watch-policy-account-link" onClick={onOpenRailAccounts}>
              {isProviderReverificationPending ? "운영사 상태 확인" : "로그인 필요"}
            </button>
          ) : null}
        </div>
        {seatFoundAction && <div className="watch-booking-action">{seatFoundAction}</div>}
        <div className="watch-control-actions">
          {watch.status === "paused" && <button type="button" className="icon-button" aria-label="대기 재개" onClick={() => onResume(watch.id)}><Play size={20} /></button>}
          {pausableWatchStatuses.has(watch.status) && <button type="button" className="icon-button" aria-label="대기 일시정지" onClick={() => onPause(watch.id)}><Pause size={20} /></button>}
          <button type="button" className="icon-button danger" aria-label="대기 취소" onClick={() => onCancel(watch.id)}><Trash size={20} /></button>
        </div>
      </div>
    </article>
  );
}

export function ActiveWatchList({
  watches,
  isRefreshing = false,
  lastRefreshedAt = null,
  onCreate,
  onViewAll,
  onRefresh,
  onPause,
  onResume,
  onCancel,
  onOpenRailAccounts,
  onChangeReservationPolicy,
  reservationPolicyUpdatingIds = new Set<string>(),
  renderSeatFoundAction,
}: ActiveWatchListProps) {
  const lastRefreshedLabel = lastRefreshedAt === null
    ? "최근 갱신 --:--:--"
    : `최근 갱신 ${formatRefreshTime(lastRefreshedAt)}`;

  return (
    <section className="active-section" aria-labelledby="active-title">
      <div className="section-heading">
        <h2 id="active-title">활동 중인 대기</h2>
        <span aria-label={`전체 ${watches.length}건 모두 표시 중`}>전체 {watches.length}건</span>
        <div className="active-refresh-status">
          <button
            type="button"
            className="icon-button"
            aria-label="활동 중인 대기 새로고침"
            aria-busy={isRefreshing}
            disabled={!onRefresh}
            onClick={onRefresh}
          >
            <ArrowsClockwise
              className={isRefreshing ? "active-refresh-icon is-spinning" : "active-refresh-icon"}
              size={21}
              aria-hidden="true"
            />
          </button>
          <span role="status" aria-live="polite">{lastRefreshedLabel}</span>
        </div>
        <button className="button button-ghost compact active-history-link" type="button" onClick={onViewAll}>전체 내역 보기</button>
      </div>
      <div className="watch-list">
        {watches.length > 0 ? watches.map((watch) => (
          <WatchRow
            key={watch.id}
            watch={watch}
            onPause={onPause}
            onResume={onResume}
            onCancel={onCancel}
            isReservationPolicyUpdating={reservationPolicyUpdatingIds.has(watch.id)}
            {...(onOpenRailAccounts ? { onOpenRailAccounts } : {})}
            {...(onChangeReservationPolicy ? { onChangeReservationPolicy } : {})}
            {...(renderSeatFoundAction ? { renderSeatFoundAction } : {})}
          />
        )) : (
          <div className="empty-state"><Clock size={32} /><strong>진행 중인 대기가 없습니다</strong><span>원하는 열차를 등록하면 이곳에서 상태를 확인할 수 있어요.</span></div>
        )}
      </div>
      <button className="button button-outline button-new-wide" type="button" onClick={onCreate}>
        <Plus size={22} /> 새 대기 만들기
      </button>
    </section>
  );
}

function formatRefreshTime(value: Date): string {
  return new Intl.DateTimeFormat("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(value);
}
