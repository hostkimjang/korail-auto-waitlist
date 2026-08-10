import { useEffect, useState } from "react";

import { paymentDeadlineInstant } from "../domain/paymentDeadline";

export function usePaymentDeadlineClock(
  values: ReadonlyArray<string | null | undefined>,
): number {
  const [now, setNow] = useState(() => Date.now());
  const [visible, setVisible] = useState(() => documentIsVisible());
  const signature = values.join("\u001f");
  const hasFutureDeadline = values.some((value) => {
    const deadline = paymentDeadlineInstant(value);
    return deadline !== null && deadline > now;
  });

  useEffect(() => {
    const handleVisibilityChange = (): void => {
      const nextVisible = documentIsVisible();
      setVisible(nextVisible);
      if (nextVisible) setNow(Date.now());
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, []);

  useEffect(() => {
    if (!visible || !hasFutureDeadline) return undefined;
    const tick = (): void => setNow(Date.now());
    const firstTick = window.setTimeout(tick, 0);
    const timer = window.setInterval(tick, 1_000);
    return () => {
      window.clearTimeout(firstTick);
      window.clearInterval(timer);
    };
  }, [hasFutureDeadline, signature, visible]);

  return now;
}

function documentIsVisible(): boolean {
  return typeof document === "undefined" || document.visibilityState !== "hidden";
}
