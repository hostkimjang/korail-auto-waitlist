import { ArrowRight } from "@phosphor-icons/react";
import { useMemo, type ReactNode } from "react";

import {
  paymentDeadlineInstant,
  paymentDeadlineState,
} from "../../domain/paymentDeadline";
import { formatReservedSeats } from "../../domain/reservationAttempt";
import { formatTrainIdentity } from "../../domain/watch";
import { usePaymentDeadlineClock } from "../../hooks/usePaymentDeadlineClock";
import { PaymentDeadlineStatus } from "../../shared/ui/PaymentDeadlineStatus";
import { StatusPill } from "../../shared/ui/StatusPill";
import { reservationConfirmationDiagnosticDescriptions } from "../../shared/lib/reservationConfirmationDiagnostic";
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

const paymentConfirmationLabels: Record<
  NonNullable<PaymentRequiredViewModel["confirmationOutcome"]>,
  string
> = {
  confirmed_payment_required: "공식 내역에서 결제 대기 확인",
  confirmed_paid: "공식 내역에서 결제 완료 확인",
  not_found: "공식 내역에서 대상 예약을 찾지 못함 · 결제 상태는 확정하지 않음",
  inconclusive: "공식 내역 확인 결과 판단 불가",
  auth_required: "공식 내역 확인에 로그인 필요",
  provider_blocked: "운영사 제한으로 공식 내역 확인 불가",
};

function evidenceTimeLabel(value: string | null | undefined): string | null {
  if (!value || !Number.isFinite(Date.parse(value))) return null;
  return new Intl.DateTimeFormat("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "Asia/Seoul",
  }).format(new Date(value));
}

function paymentConfirmationEvidence(watch: PaymentRequiredViewModel): string | null {
  const outcome = watch.confirmationOutcome;
  const count = watch.reconciliationAttemptCount ?? 0;
  const observedAt = evidenceTimeLabel(watch.confirmationObservedAt);
  const nextAt = evidenceTimeLabel(watch.nextReconcileAt);
  const details = [
    outcome === "inconclusive"
      ? reservationConfirmationDiagnosticDescriptions[
          watch.confirmationDiagnosticCode ?? "unspecified"
        ]
      : outcome ? paymentConfirmationLabels[outcome] : null,
    observedAt ? `확인 ${observedAt}` : null,
    count > 0 ? `공식 재확인 ${count}/6회` : null,
    nextAt ? `다음 ${nextAt}` : null,
  ].filter((detail): detail is string => detail !== null);
  return details.length === 0 ? null : details.join(" · ");
}

function PaymentRequiredCard({ watch, onOpenPayment, now }: {
  watch: PaymentRequiredViewModel;
  onOpenPayment: PaymentRequiredSectionProps["onOpenPayment"];
  now: number;
}) {
  const [routeOrigin = "출발역", routeDestination = "도착역"] = String(watch.route ?? "").split(" → ");
  const origin = watch.origin || routeOrigin;
  const destination = watch.destination || routeDestination;
  const reservedSeatLabel = formatReservedSeats(watch.reservedSeats ?? []);
  const confirmationEvidence = paymentConfirmationEvidence(watch);
  return (
    <article className="payment-hero payment-required-card" aria-labelledby={`payment-title-${watch.id}`}>
      <div className="payment-trip">
        <StatusPill status="payment_required">결제 필요</StatusPill>
        <div className="trip-title-row">
          <h2 id={`payment-title-${watch.id}`}>{origin} <ArrowRight aria-hidden="true" /> {destination}</h2>
          <span className={`provider-chip ${watch.provider === "SRT" ? "provider-srt" : "provider-korail"}`}>
            {formatTrainIdentity(watch.trainType, watch.train)}
          </span>
        </div>
        <div className="trip-times" aria-label={`${origin} ${watch.departure} 출발, ${destination} ${watch.arrival} 도착`}>
          <div><strong>{watch.departure}</strong><span>{watch.date}</span></div>
          <ArrowRight className="time-arrow" aria-hidden="true" />
          <div><strong>{watch.arrival}</strong><span>{watch.date}</span></div>
        </div>
        {watch.seatClassLabel ? <span className="payment-seat-class">{watch.seatClassLabel} 임시 예약</span> : null}
        {reservedSeatLabel ? (
          <span className="payment-reserved-seats">예약 좌석 {reservedSeatLabel}</span>
        ) : null}
      </div>
      <div className="payment-action">
        <PaymentDeadlineStatus value={watch.paymentDeadline} now={now} />
        {confirmationEvidence ? (
          <p className="payment-confirmation-evidence" role="note">
            {confirmationEvidence}
          </p>
        ) : null}
        <button
          className="button button-primary button-payment"
          type="button"
          disabled={!watch.officialBookingUrl}
          onClick={() => onOpenPayment(watch)}
        >
          공식 결제 열기 <ArrowRight size={22} aria-hidden="true" />
        </button>
        {watch.provider === "SRT" ? (
          <p className="payment-srt-refresh-hint" role="note">
            방금 예약이 비어 보이면 하단 ‘승차권 확인’을 한 번 더 눌러 목록을 갱신하세요.
          </p>
        ) : null}
      </div>
    </article>
  );
}

export function PaymentRequiredSection({
  watches,
  onOpenPayment,
  emptyState = null,
}: PaymentRequiredSectionProps) {
  const paymentActionableWatches = useMemo(
    () => watches.filter((watch) => watch.confirmationOutcome !== "confirmed_paid"),
    [watches],
  );
  const now = usePaymentDeadlineClock(
    paymentActionableWatches.map((watch) => watch.paymentDeadline),
  );
  const sorted = useMemo(
    () => sortPaymentRequiredWatches(
      paymentActionableWatches.filter((watch) => (
        paymentDeadlineState(watch.paymentDeadline, now) !== "elapsed"
      )),
    ),
    [now, paymentActionableWatches],
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
