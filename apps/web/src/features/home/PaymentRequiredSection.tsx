import { ArrowRight } from "@phosphor-icons/react";
import { useMemo, type ReactNode } from "react";

import {
  paymentDeadlineInstant,
  paymentDeadlineState,
} from "../../domain/paymentDeadline";
import { usePaymentDeadlineClock } from "../../hooks/usePaymentDeadlineClock";
import { PaymentDeadlineStatus } from "../../shared/ui/PaymentDeadlineStatus";
import { StatusPill } from "../../shared/ui/StatusPill";

export interface PaymentRequiredWatch {
  id?: string;
  provider: "KORAIL" | "SRT" | "MOCK";
  train: string;
  origin?: string;
  destination?: string;
  route?: string;
  departure: string;
  arrival: string;
  date: string;
  seatClassLabel?: string;
  payment_deadline?: string | null;
  official_booking_url?: string | null;
}

function watchIdentity(watch: PaymentRequiredWatch): string {
  return watch.id ?? `${watch.provider}-${watch.train}-${watch.date}-${watch.departure}`;
}

export interface PaymentRequiredSectionProps {
  watches: ReadonlyArray<PaymentRequiredWatch>;
  onOpenPayment: (watch: PaymentRequiredWatch) => void;
  emptyState?: ReactNode;
}

export function sortPaymentRequiredWatches(
  watches: ReadonlyArray<PaymentRequiredWatch>,
): PaymentRequiredWatch[] {
  return [...watches].sort((left, right) => {
    const leftDeadline = paymentDeadlineInstant(left.payment_deadline);
    const rightDeadline = paymentDeadlineInstant(right.payment_deadline);
    if (leftDeadline === null && rightDeadline === null) return watchIdentity(left).localeCompare(watchIdentity(right));
    if (leftDeadline === null) return 1;
    if (rightDeadline === null) return -1;
    return leftDeadline - rightDeadline || watchIdentity(left).localeCompare(watchIdentity(right));
  });
}

function PaymentRequiredCard({ watch, onOpenPayment, now }: {
  watch: PaymentRequiredWatch;
  onOpenPayment: PaymentRequiredSectionProps["onOpenPayment"];
  now: number;
}) {
  const identity = watchIdentity(watch);
  const [routeOrigin = "출발역", routeDestination = "도착역"] = String(watch.route ?? "").split(" → ");
  const origin = watch.origin || routeOrigin;
  const destination = watch.destination || routeDestination;
  return (
    <article className="payment-hero payment-required-card" aria-labelledby={`payment-title-${identity}`}>
      <div className="payment-trip">
        <StatusPill status="payment_required">결제 필요</StatusPill>
        <div className="trip-title-row">
          <h2 id={`payment-title-${identity}`}>{origin} <ArrowRight aria-hidden="true" /> {destination}</h2>
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
        <PaymentDeadlineStatus value={watch.payment_deadline} now={now} />
        <button
          className="button button-primary button-payment"
          type="button"
          disabled={!watch.official_booking_url}
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
  const now = usePaymentDeadlineClock(watches.map((watch) => watch.payment_deadline));
  const sorted = useMemo(
    () => sortPaymentRequiredWatches(
      watches.filter((watch) => paymentDeadlineState(watch.payment_deadline, now) !== "elapsed"),
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
            key={watchIdentity(watch)}
            watch={watch}
            onOpenPayment={onOpenPayment}
            now={now}
          />
        ))}
      </div>
    </section>
  );
}
