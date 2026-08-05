import type { WatchReadModel } from "../../api/watchProjection";
import type { WatchProvider } from "../../domain/watch";

export interface PaymentRequiredViewModel {
  id: string;
  provider: WatchProvider;
  train: string;
  origin: string | null;
  destination: string | null;
  route: string | null;
  departure: string;
  arrival: string;
  date: string;
  seatClassLabel: string | null;
  paymentDeadline: string | null;
  officialBookingUrl: string | null;
}

export interface LegacyPaymentRequiredWatch {
  id?: string;
  provider: WatchProvider;
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

function legacyPaymentRequiredWatchId(watch: LegacyPaymentRequiredWatch): string {
  return watch.id ?? `${watch.provider}-${watch.train}-${watch.date}-${watch.departure}`;
}

export function mapPaymentRequiredWatch(watch: WatchReadModel): PaymentRequiredViewModel {
  return {
    id: watch.id,
    provider: watch.provider,
    train: watch.train,
    origin: watch.origin,
    destination: watch.destination,
    route: watch.route,
    departure: watch.departure,
    arrival: watch.arrival,
    date: watch.date,
    seatClassLabel: watch.seatClassLabel,
    paymentDeadline: watch.paymentDeadline,
    officialBookingUrl: watch.officialBookingUrl,
  };
}

export function mapLegacyPaymentRequiredWatch(
  watch: LegacyPaymentRequiredWatch,
): PaymentRequiredViewModel {
  return {
    id: legacyPaymentRequiredWatchId(watch),
    provider: watch.provider,
    train: watch.train,
    origin: watch.origin ?? null,
    destination: watch.destination ?? null,
    route: watch.route ?? null,
    departure: watch.departure,
    arrival: watch.arrival,
    date: watch.date,
    seatClassLabel: watch.seatClassLabel ?? null,
    paymentDeadline: watch.payment_deadline ?? null,
    officialBookingUrl: watch.official_booking_url ?? null,
  };
}
