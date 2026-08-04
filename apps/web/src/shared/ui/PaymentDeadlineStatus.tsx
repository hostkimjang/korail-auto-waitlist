import { paymentDeadlineInstant, paymentDeadlineState } from "../../domain/paymentDeadline";

export interface PaymentDeadlineStatusProps {
  value?: string | null | undefined;
  now: number;
}

function formatDuration(seconds: number): string {
  const hours = String(Math.floor(seconds / 3600)).padStart(2, "0");
  const minutes = String(Math.floor((seconds % 3600) / 60)).padStart(2, "0");
  const rest = String(seconds % 60).padStart(2, "0");
  return `${hours}:${minutes}:${rest}`;
}

export function PaymentDeadlineStatus({ value, now }: PaymentDeadlineStatusProps) {
  const deadline = paymentDeadlineInstant(value);

  if (deadline === null) {
    return (
      <>
        <strong>결제기한 미제공</strong>
        <span>공식 플랫폼에서 결제기한을 직접 확인해 주세요</span>
      </>
    );
  }

  const state = paymentDeadlineState(value, now);
  const deadlineTime = new Intl.DateTimeFormat("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "Asia/Seoul",
  }).format(deadline);
  if (state === "elapsed") {
    return (
      <>
        <strong>결제기한 경과 · 공식 확인 필요</strong>
        <span>{deadlineTime} 기한 · 공식 예약 내역에서 상태를 확인해 주세요</span>
      </>
    );
  }

  const remainingSeconds = Math.max(0, Math.floor((deadline - now) / 1_000));
  const minuteLabel = `${Math.max(1, Math.ceil(remainingSeconds / 60))}분 내 결제`;

  return (
    <>
      <strong>{minuteLabel}</strong>
      <span>{deadlineTime}까지 결제해 주세요</span>
      <time className="countdown" dateTime={value ?? undefined} aria-live="polite">
        {formatDuration(remainingSeconds)}
      </time>
    </>
  );
}
