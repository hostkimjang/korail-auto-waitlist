export type PaymentDeadlineState = "missing" | "active" | "elapsed";

export function paymentDeadlineInstant(value: string | null | undefined): number | null {
  if (!value || !/(?:Z|[+-]\d{2}:\d{2})$/i.test(value)) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function paymentDeadlineState(
  value: string | null | undefined,
  now: number,
): PaymentDeadlineState {
  const deadline = paymentDeadlineInstant(value);
  if (deadline === null) return "missing";
  return deadline <= now ? "elapsed" : "active";
}
