export type ReservationPolicy = "notify_only" | "reserve_once_before_payment";

export function normalizeReservationPolicy(value: unknown): ReservationPolicy {
  return value === "reserve_once_before_payment"
    ? "reserve_once_before_payment"
    : "notify_only";
}
