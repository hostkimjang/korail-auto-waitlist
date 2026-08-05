import { paymentDeadlineState } from "../../domain/paymentDeadline";
import { usePaymentDeadlineClock } from "../../hooks/usePaymentDeadlineClock";
import type { ReservationWatchViewModel } from "./reservationViewModel";

export interface ReservationSummaryProps {
  watches: ReadonlyArray<ReservationWatchViewModel>;
}

export function ReservationSummary({ watches }: ReservationSummaryProps) {
  const now = usePaymentDeadlineClock(watches.map((watch) => watch.paymentDeadline));
  const activeCount = watches.filter(
    (watch) => !["payment_required", "completed", "expired", "failed"].includes(watch.status),
  ).length;
  const paymentCount = watches.filter(
    (watch) => watch.status === "payment_required"
      && paymentDeadlineState(watch.paymentDeadline, now) !== "elapsed",
  ).length;
  const elapsedPaymentCount = watches.filter(
    (watch) => watch.status === "payment_required"
      && paymentDeadlineState(watch.paymentDeadline, now) === "elapsed",
  ).length;
  const completedCount = watches.filter((watch) => watch.status === "completed").length;

  return (
    <div className="reservation-summary">
      <div><span>진행 중</span><strong>{activeCount}</strong></div>
      <div><span>결제 필요</span><strong className="orange">{paymentCount}</strong></div>
      {elapsedPaymentCount > 0 ? (
        <div><span>기한 경과 확인</span><strong>{elapsedPaymentCount}</strong></div>
      ) : null}
      <div><span>완료</span><strong>{completedCount}</strong></div>
    </div>
  );
}
