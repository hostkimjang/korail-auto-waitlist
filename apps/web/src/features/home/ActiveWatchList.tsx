import { ArrowRight, ArrowsClockwise, Clock, Pause, Play, Plus, Trash } from "@phosphor-icons/react";
import type { ReactElement, ReactNode } from "react";

import type { ReservationPolicy } from "../../domain/reservationPolicy";
import { StatusPill } from "../../shared/ui/StatusPill";
import {
  activeWatchRefreshLabel,
  presentActiveWatchRow,
  type ActiveWatch,
} from "./activeWatchViewModel";

export type { WatchStatus } from "../../domain/watch";
export type {
  ActiveWatch,
  RailAccountAuthStatus,
  SeatFoundObservationContext,
} from "./activeWatchViewModel";

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

export function WatchRow({
  watch,
  onPause,
  onResume,
  onCancel,
  onOpenRailAccounts,
  onChangeReservationPolicy,
  isReservationPolicyUpdating = false,
  renderSeatFoundAction,
}: WatchRowProps): ReactElement {
  const presentation = presentActiveWatchRow(watch, isReservationPolicyUpdating);
  const seatFoundAction = presentation.canRenderSeatFoundAction
    ? renderSeatFoundAction?.(watch)
    : null;
  return (
    <article className={presentation.isAuthRequired ? "watch-row is-auth-required" : "watch-row"}>
      <div className="watch-provider">
        <span className={`provider-chip ${watch.provider === "SRT" ? "provider-srt" : "provider-korail"}`}>{watch.provider}</span>
        <div><strong>{watch.route}</strong><span>{watch.train} · {watch.date}</span></div>
      </div>
      <div className="watch-time"><strong>{watch.departure}</strong><ArrowRight size={18} /><strong>{watch.arrival}</strong></div>
      <div className="watch-state">
        {presentation.isAuthRequired ? (
          <div className="watch-auth-line">
            <StatusPill status={watch.status}>{presentation.statusLabel}</StatusPill>
            <span className="watch-auth-summary">{presentation.authSummary}</span>
            {onOpenRailAccounts ? (
              <button type="button" className="button button-outline compact watch-auth-action" onClick={onOpenRailAccounts}>
                {presentation.isProviderReverificationPending ? "운영사 상태" : "철도 계정"}
              </button>
            ) : null}
          </div>
        ) : <StatusPill status={watch.status}>{presentation.statusLabel}</StatusPill>}
        <span className="watch-seat-evidence">{presentation.seatEvidenceLabel}</span>
        {presentation.shouldShowRegistrationEvidence
          ? <span className="watch-registration-evidence">등록 당시 {watch.registrationEvidenceLabel}</span>
          : null}
        {watch.operational ? (
          <span
            className={`watch-operational is-${watch.operational.status}${watch.operational.fresh ? " is-fresh" : ""}`}
          >
            {watch.operational.label}
          </span>
        ) : null}
        {presentation.reservationAttemptLabel ? (
          <span className="watch-reservation-attempt">
            {presentation.reservationAttemptLabel}
          </span>
        ) : null}
        <span className="watch-last-checked">{watch.lastCheckedLabel ?? "최근 확인 기록 없음"}</span>
        {presentation.nextCheckLabel ? (
          <span className="watch-next-check">{presentation.nextCheckLabel}</span>
        ) : null}
      </div>
      <div className="row-actions">
        <div className="watch-policy-control">
          <span className="watch-policy-label">
            {presentation.automaticReservationEnabled ? "좌석 재발견마다 자동 예매" : "감시만"}
          </span>
          <button
            type="button"
            role="switch"
            aria-label={`${watch.train} ${watch.seatClassLabel} 좌석 재발견마다 자동 예매 설정`}
            aria-checked={presentation.automaticReservationEnabled}
            aria-busy={isReservationPolicyUpdating}
            className={presentation.automaticReservationEnabled ? "watch-policy-switch is-on" : "watch-policy-switch"}
            disabled={presentation.policySwitchDisabled || !onChangeReservationPolicy}
            title={presentation.policySwitchTitle}
            onClick={() => onChangeReservationPolicy?.(watch.id, presentation.nextReservationPolicy)}
          >
            <span aria-hidden="true" />
          </button>
          {presentation.shouldShowPolicyAccountLink && onOpenRailAccounts ? (
            <button type="button" className="watch-policy-account-link" onClick={onOpenRailAccounts}>
              {presentation.policyAccountLinkLabel}
            </button>
          ) : null}
        </div>
        {seatFoundAction && <div className="watch-booking-action">{seatFoundAction}</div>}
        <div className="watch-control-actions">
          {watch.status === "paused" && <button type="button" className="icon-button" aria-label="대기 재개" onClick={() => onResume(watch.id)}><Play size={20} /></button>}
          {presentation.canPause && <button type="button" className="icon-button" aria-label="대기 일시정지" onClick={() => onPause(watch.id)}><Pause size={20} /></button>}
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
}: ActiveWatchListProps): ReactElement {
  const lastRefreshedLabel = activeWatchRefreshLabel(lastRefreshedAt);

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
