import { ArrowRight } from "@phosphor-icons/react";
import { useMemo, type ReactNode } from "react";

import {
  paymentDeadlineInstant,
  paymentDeadlineState,
} from "../../domain/paymentDeadline";
import { usePaymentDeadlineClock } from "../../hooks/usePaymentDeadlineClock";
import { PaymentDeadlineStatus } from "../../shared/ui/PaymentDeadlineStatus";
import { StatusPill } from "../../shared/ui/StatusPill";
import type { PaymentRequiredViewModel } from "./paymentRequiredViewModel";

export type {
  LegacyPaymentRequiredWatch as PaymentRequiredWatch,
  PaymentRequiredViewModel,
} from "./paymentRequiredViewModel";

export interface PaymentRequiredSectionProps {
  watches: ReadonlyArray<PaymentRequiredViewModel>;
  onOpenPayment: (watch: PaymentRequiredViewModel) => void;
  emptyState?: ReactNode;
}

export function sortPaymentRequiredWatches(
  watches: ReadonlyArray<PaymentRequiredViewModel>,
): PaymentRequiredViewModel[] {
  return [...watches].sort((left, right) => {
    const leftDeadline = paymentDeadlineInstant(left.paymentDeadline);
    const rightDeadline = paymentDeadlineInstant(right.paymentDeadline);
    if (leftDeadline === null && rightDeadline === null) return left.id.localeCompare(right.id);
    if (leftDeadline === null) return 1;
    if (rightDeadline === null) return -1;
    return leftDeadline - rightDeadline || left.id.localeCompare(right.id);
  });
}

function PaymentRequiredCard({ watch, onOpenPayment, now }: {
  watch: PaymentRequiredViewModel;
  onOpenPayment: PaymentRequiredSectionProps["onOpenPayment"];
  now: number;
}) {
  const [routeOrigin = "출발역", routeDestination = "도착역"] = String(watch.route ?? "").split(" → ");
  const origin = watch.origin || routeOrigin;
  const destination = watch.destination || routeDestination;
  return (
    <article className="payment-hero payment-required-card" aria-labelledby={`payment-title-${watch.id}`}>
      <div className="payment-trip">
        <StatusPill status="payment_required">결제 필요</StatusPill>
        <div className="trip-title-row">
          <h2 id={`payment-title-${watch.id}`}>{origin} <ArrowRight aria-hidden="true" /> {destination}</h2>
          <span className={`provider-chip ${watch.provider === "SRT" ? "provider-srt" : "provider-korail"}`}>{watch.train}</span>
        </div>
        <div className="trip-times" aria-label={`${origin} ${watch.departure} 출발, ${destination} ${watch.arrival} 도착`}>
          <div><strong>{watch.departure}</strong><span>{watch.date}</span></div>
          <ArrowRight className="time-arrow" aria-hidden="true" />
          <div><strong>{watch.arrival}</strong><span>{watch.date}</span></div>
        </div>
        {watch.seatClassLabel ? <span className="payment-seat-class">{watch.seatClassLabel} 임시 예약</span> : null}
      </div>
      <div className="payment-action">
        <PaymentDeadlineStatus value={watch.paymentDeadline} now={now} />
        <button
          className="button button-primary button-payment"
          type="button"
          disabled={!watch.officialBookingUrl}
          onClick={() => onOpenPayment(watch)}
        >
          공식 결제 열기 <ArrowRight size={22} aria-hidden="true" />
        </button>
      </div>
    </article>
  );
}

export function PaymentRequiredSection({
  watches,
  onOpenPayment,
  emptyState = null,
}: PaymentRequiredSectionProps) {
  const now = usePaymentDeadlineClock(watches.map((watch) => watch.paymentDeadline));
  const sorted = useMemo(
    () => sortPaymentRequiredWatches(
      watches.filter((watch) => paymentDeadlineState(watch.paymentDeadline, now) !== "elapsed"),
    ),
    [now, watches],
  );
  if (sorted.length === 0) return emptyState;
  return (
    <section className="payment-required-section" aria-labelledby="payment-required-heading">
      <header>
        <div><span>지금 확인하세요</span><h2 id="payment-required-heading">결제 대기 {sorted.length}건</h2></div>
        <p>예매는 결제 직전까지 진행되었습니다. 결제는 공식 플랫폼에서 직접 완료하세요.</p>
      </header>
      <div className="payment-required-list">
        {sorted.map((watch) => (
          <PaymentRequiredCard
            key={watch.id}
            watch={watch}
            onOpenPayment={onOpenPayment}
            now={now}
          />
        ))}
      </div>
    </section>
  );
}
