import type { WatchReadModel } from "../../api/watchProjection";
import type {
  ReservationConfirmationDiagnosticCode,
  ReservationConfirmationOutcome,
  ReservedSeat,
} from "../../domain/reservationAttempt";
import type { WatchProvider } from "../../domain/watch";

export interface PaymentRequiredViewModel {
  id: string;
  provider: WatchProvider;
  train: string;
  trainType?: string | null;
  origin: string | null;
  destination: string | null;
  route: string | null;
  departure: string;
  arrival: string;
  date: string;
  seatClassLabel: string | null;
  reservedSeats?: ReadonlyArray<ReservedSeat>;
  paymentDeadline: string | null;
  officialBookingUrl: string | null;
  confirmationOutcome?: ReservationConfirmationOutcome | null;
  confirmationDiagnosticCode?: ReservationConfirmationDiagnosticCode | null;
  confirmationObservedAt?: string | null;
  reconciliationAttemptCount?: number;
  nextReconcileAt?: string | null;
}

export interface LegacyPaymentRequiredWatch {
  id?: string;
  provider: WatchProvider;
  train: string;
  trainType?: string | null;
  origin?: string;
  destination?: string;
  route?: string;
  departure: string;
  arrival: string;
  date: string;
  seatClassLabel?: string;
  reservedSeats?: ReadonlyArray<ReservedSeat>;
  payment_deadline?: string | null;
  official_booking_url?: string | null;
}

function legacyPaymentRequiredWatchId(watch: LegacyPaymentRequiredWatch): string {
  return watch.id ?? `${watch.provider}-${watch.train}-${watch.date}-${watch.departure}`;
}

export function mapPaymentRequiredWatch(watch: WatchReadModel): PaymentRequiredViewModel {
  return {
    id: watch.id,
    provider: watch.provider,
    train: watch.train,
    trainType: watch.trainType ?? null,
    origin: watch.origin,
    destination: watch.destination,
    route: watch.route,
    departure: watch.departure,
    arrival: watch.arrival,
    date: watch.date,
    seatClassLabel: watch.seatClassLabel,
    reservedSeats: watch.paymentRequiredReservedSeats,
    paymentDeadline: watch.paymentDeadline,
    officialBookingUrl: watch.officialBookingUrl,
    confirmationOutcome: watch.paymentRequiredReservationAttempt?.confirmationOutcome ?? null,
    confirmationDiagnosticCode:
      watch.paymentRequiredReservationAttempt?.confirmationDiagnosticCode ?? null,
    confirmationObservedAt:
      watch.paymentRequiredReservationAttempt?.confirmationObservedAt ?? null,
    reconciliationAttemptCount:
      watch.paymentRequiredReservationAttempt?.reconciliationAttemptCount ?? 0,
    nextReconcileAt: watch.paymentRequiredReservationAttempt?.nextReconcileAt ?? null,
  };
}

export function mapLegacyPaymentRequiredWatch(
  watch: LegacyPaymentRequiredWatch,
): PaymentRequiredViewModel {
  return {
    id: legacyPaymentRequiredWatchId(watch),
    provider: watch.provider,
    train: watch.train,
    trainType: watch.trainType ?? null,
    origin: watch.origin ?? null,
    destination: watch.destination ?? null,
    route: watch.route ?? null,
    departure: watch.departure,
    arrival: watch.arrival,
    date: watch.date,
    seatClassLabel: watch.seatClassLabel ?? null,
    reservedSeats: watch.reservedSeats ?? [],
    paymentDeadline: watch.payment_deadline ?? null,
    officialBookingUrl: watch.official_booking_url ?? null,
    confirmationOutcome: null,
    confirmationDiagnosticCode: null,
    confirmationObservedAt: null,
    reconciliationAttemptCount: 0,
    nextReconcileAt: null,
  };
}
