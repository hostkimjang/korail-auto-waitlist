import { useEffect, useState } from "react";

import { paymentDeadlineInstant } from "../domain/paymentDeadline";

export function usePaymentDeadlineClock(
  values: ReadonlyArray<string | null | undefined>,
): number {
  const [now, setNow] = useState(() => Date.now());
  const signature = values.join("\u001f");
  const hasFutureDeadline = values.some((value) => {
    const deadline = paymentDeadlineInstant(value);
    return deadline !== null && deadline > now;
  });

  useEffect(() => {
    setNow(Date.now());
    if (!hasFutureDeadline) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [hasFutureDeadline, signature]);

  return now;
}
