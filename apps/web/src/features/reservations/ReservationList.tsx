import { ArrowSquareOut, Ticket, Trash } from "@phosphor-icons/react";
import { useMemo } from "react";

import {
  paymentDeadlineInstant,
  paymentDeadlineState,
} from "../../domain/paymentDeadline";
import { usePaymentDeadlineClock } from "../../hooks/usePaymentDeadlineClock";
import { PaymentDeadlineStatus } from "../../shared/ui/PaymentDeadlineStatus";
import { StatusPill } from "../../shared/ui/StatusPill";
import type { ReservationWatchViewModel } from "./reservationViewModel";

export type {
  LegacyReservationListWatch as ReservationListWatch,
} from "./reservationViewModel";

export interface ReservationListProps {
  watches: ReadonlyArray<ReservationWatchViewModel>;
  onCreate: () => void;
  onOpenOfficial: (watch: ReservationWatchViewModel) => void;
  onDelete?: (watchId: string) => void;
}

export function sortReservationWatches(
  watches: ReadonlyArray<ReservationWatchViewModel>,
  now = Date.now(),
): ReservationWatchViewModel[] {
  return watches
    .map((watch, index) => ({ watch, index }))
    .sort((left, right) => {
      const leftPayment = left.watch.status === "payment_required";
      const rightPayment = right.watch.status === "payment_required";
      const leftElapsed = leftPayment
        && paymentDeadlineState(left.watch.paymentDeadline, now) === "elapsed";
      const rightElapsed = rightPayment
        && paymentDeadlineState(right.watch.paymentDeadline, now) === "elapsed";
      const leftRank = leftPayment ? (leftElapsed ? 2 : 0) : 1;
      const rightRank = rightPayment ? (rightElapsed ? 2 : 0) : 1;
      if (leftRank !== rightRank) return leftRank - rightRank;
      if (!leftPayment || leftElapsed) return left.index - right.index;

      const leftDeadline = paymentDeadlineInstant(left.watch.paymentDeadline);
      const rightDeadline = paymentDeadlineInstant(right.watch.paymentDeadline);
      if (leftDeadline === null && rightDeadline !== null) return 1;
      if (leftDeadline !== null && rightDeadline === null) return -1;
      if (leftDeadline !== null && rightDeadline !== null && leftDeadline !== rightDeadline) {
        return leftDeadline - rightDeadline;
      }
      return left.index - right.index;
    })
    .map(({ watch }) => watch);
}

export function ReservationList({ watches, onCreate, onOpenOfficial, onDelete }: ReservationListProps) {
  const now = usePaymentDeadlineClock(watches.map((watch) => watch.paymentDeadline));
  const sortedWatches = useMemo(() => sortReservationWatches(watches, now), [now, watches]);

  return (
    <section className="reservation-list">
      {sortedWatches.length === 0 ? (
        <div className="empty-state">
          <Ticket size={32} />
          <strong>등록된 대기가 없습니다</strong>
          <span>새 대기를 만들면 실제 상태가 이곳에 표시됩니다.</span>
          <button type="button" className="button button-primary compact" onClick={onCreate}>새 대기 만들기</button>
        </div>
      ) : null}
      {sortedWatches.map((watch) => {
        const isPayment = watch.status === "payment_required";
        const isElapsedPayment = isPayment
          && paymentDeadlineState(watch.paymentDeadline, now) === "elapsed";
        const showOfficialLink = Boolean(
          (isPayment || watch.status === "scheduled") && watch.officialBookingUrl,
        );
        const showSrtTicketRefreshHint = showOfficialLink
          && isPayment
          && watch.provider === "SRT";
        const canDelete = ["draft", "expired", "failed"].includes(watch.status);
        return (
          <article
            key={watch.id}
            className={isPayment
              ? `reservation-item ${isElapsedPayment ? "is-payment-expired" : "is-payment"}`
              : "reservation-item"}
          >
            <StatusPill status={isElapsedPayment ? "payment_expired" : watch.status}>
              {isElapsedPayment ? "기한 경과 · 확인 필요" : watch.statusLabel}
            </StatusPill>
            <div>
              <h2>{watch.route}</h2>
              <span>{watch.train} · {watch.date} · {watch.departure} 출발</span>
            </div>
            {isPayment ? (
              <div className="reservation-payment-deadline">
                <PaymentDeadlineStatus value={watch.paymentDeadline} now={now} />
              </div>
            ) : null}
            {showOfficialLink ? (
              <button
                type="button"
                className={`button ${isPayment && !isElapsedPayment ? "button-primary" : "button-outline"} compact`}
                onClick={() => onOpenOfficial(watch)}
              >
                {isElapsedPayment
                  ? "공식 확인 열기"
                  : isPayment ? "결제 열기" : "공식 예매 열기"}<ArrowSquareOut />
              </button>
            ) : null}
            {showSrtTicketRefreshHint ? (
              <p className="reservation-srt-refresh-hint" role="note">
                <strong>SRT 앱 갱신 안내</strong>
                <span>방금 예약이 비어 보이면 하단 ‘승차권 확인’을 한 번 더 눌러 목록을 갱신하세요.</span>
              </p>
            ) : null}
            {canDelete ? (
              <button
                type="button"
                className="button button-ghost compact"
                onClick={() => onDelete?.(watch.id)}
              >
                기록 삭제<Trash />
              </button>
            ) : null}
          </article>
        );
      })}
    </section>
  );
}
