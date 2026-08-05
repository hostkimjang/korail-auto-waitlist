import {
  safeOfficialChannelUrl,
  type WatchReadModel,
} from "../../api/watchProjection";
import { isWatchStatus, type WatchStatus } from "../../domain/watch";

export type ReservationDisplayStatus = WatchStatus | "unknown";

export interface ReservationWatchViewModel {
  id: string;
  status: ReservationDisplayStatus;
  statusLabel: string;
  route: string;
  train: string;
  date: string;
  departure: string;
  paymentDeadline: string | null;
  officialBookingUrl: string | null;
}

export interface LegacyReservationListWatch {
  id: string;
  status: string;
  statusLabel: string;
  route: string;
  train: string;
  date: string;
  departure: string;
  payment_deadline?: string | null;
  official_booking_url?: string | null;
}

export function mapReservationWatch(watch: WatchReadModel): ReservationWatchViewModel {
  return {
    id: watch.id,
    status: watch.status,
    statusLabel: watch.statusLabel,
    route: watch.route,
    train: watch.train,
    date: watch.date,
    departure: watch.departure,
    paymentDeadline: watch.paymentDeadline,
    officialBookingUrl: watch.officialBookingUrl,
  };
}

export function mapLegacyReservationWatch(
  watch: LegacyReservationListWatch,
): ReservationWatchViewModel {
  return {
    id: watch.id,
    status: isWatchStatus(watch.status) ? watch.status : "unknown",
    statusLabel: watch.statusLabel,
    route: watch.route,
    train: watch.train,
    date: watch.date,
    departure: watch.departure,
    paymentDeadline: watch.payment_deadline ?? null,
    officialBookingUrl: safeOfficialChannelUrl(watch.official_booking_url),
  };
}
