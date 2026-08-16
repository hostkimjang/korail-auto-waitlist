import { ArrowClockwise, WarningCircle, X } from "@phosphor-icons/react";
import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactElement,
} from "react";
import { createPortal } from "react-dom";

import { useDocumentScrollLock } from "../../hooks/useDocumentScrollLock";
import type {
  ManualRearmReason,
  PaymentHoldEndReason,
} from "../../domain/reservationAttempt";

export type ReservationRearmMode =
  | {
    kind: "payment_hold";
    reason: "payment_hold_ended";
    paymentHoldEndReason: PaymentHoldEndReason;
  }
  | {
    kind: "unknown";
    reason: "unknown_result_unresolved";
  };

export interface ReservationRearmConfirmProps {
  watchId: string;
  trainLabel: string;
  travelDate: string;
  departure: string;
  seatClassLabel: string;
  mode: ReservationRearmMode;
  mutationPending?: boolean;
  onConfirm: (watchId: string, reason: ManualRearmReason) => void | Promise<void>;
}

function restoreFocus(
  trigger: HTMLButtonElement | null,
  fallbackRow: HTMLElement | null,
): void {
  window.setTimeout(() => {
    if (trigger?.isConnected) {
      trigger.focus();
      return;
    }
    if (fallbackRow?.isConnected) fallbackRow.focus();
  }, 0);
}

export function ReservationRearmConfirm({
  watchId,
  trainLabel,
  travelDate,
  departure,
  seatClassLabel,
  mode,
  mutationPending = false,
  onConfirm,
}: ReservationRearmConfirmProps): ReactElement {
  const [open, setOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [absenceConfirmed, setAbsenceConfirmed] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const safeId = watchId.replace(/[^a-zA-Z0-9_-]/g, "-");
  const titleId = `reservation-rearm-title-${safeId}`;
  const descriptionId = `reservation-rearm-description-${safeId}`;
  const pending = submitting || mutationPending;
  useDocumentScrollLock(open);

  const close = (): void => {
    if (pending) return;
    const trigger = triggerRef.current;
    const fallbackRow = trigger?.closest<HTMLElement>(".watch-row") ?? null;
    setOpen(false);
    setAbsenceConfirmed(false);
    restoreFocus(trigger, fallbackRow);
  };

  useEffect(() => {
    if (!open) return undefined;
    const appRoot = triggerRef.current?.closest<HTMLElement>(".app-shell");
    const previousInert = appRoot?.inert ?? false;
    const previousAriaHidden = appRoot?.getAttribute("aria-hidden") ?? null;
    if (appRoot) {
      appRoot.inert = true;
      appRoot.setAttribute("aria-hidden", "true");
    }
    const focusTimer = window.setTimeout(() => {
      dialogRef.current?.querySelector<HTMLElement>("[data-autofocus]")?.focus();
    }, 0);
    return () => {
      window.clearTimeout(focusTimer);
      if (!appRoot) return;
      appRoot.inert = previousInert;
      if (previousAriaHidden === null) appRoot.removeAttribute("aria-hidden");
      else appRoot.setAttribute("aria-hidden", previousAriaHidden);
    };
  }, [open]);

  const confirm = async (): Promise<void> => {
    if (pending || !absenceConfirmed) return;
    const trigger = triggerRef.current;
    const fallbackRow = trigger?.closest<HTMLElement>(".watch-row") ?? null;
    setSubmitting(true);
    try {
      await onConfirm(watchId, mode.reason);
      setOpen(false);
      setAbsenceConfirmed(false);
      restoreFocus(trigger, fallbackRow);
    } catch {
      // 호출자가 사용자 피드백을 표시하며, 실패 시 같은 확인 dialog를 유지합니다.
    } finally {
      setSubmitting(false);
    }
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLElement>): void => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== "Tab" || !dialogRef.current) return;
    const focusable = [...dialogRef.current.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input:not(:disabled), [href], [tabindex]:not([tabindex="-1"])',
    )];
    const first = focusable[0];
    const last = focusable.at(-1);
    if (!first || !last) return;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className="button button-outline compact watch-rearm-button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-busy={mutationPending}
        disabled={mutationPending}
        onClick={() => {
          setAbsenceConfirmed(false);
          setOpen(true);
        }}
      >
        <ArrowClockwise aria-hidden="true" /> 자동 예매 다시 시도
      </button>
      {open && createPortal((
        <div className="official-handoff-layer reservation-rearm-layer">
          <div className="official-handoff-scrim" aria-hidden="true" onClick={close} />
          <section
            ref={dialogRef}
            className="official-handoff-panel reservation-rearm-panel"
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            aria-describedby={descriptionId}
            aria-busy={pending}
            onKeyDown={handleKeyDown}
          >
            <header className="official-handoff-header">
              <div>
                <span className="reservation-rearm-eyebrow">사용자 확인 필요</span>
                <h2 id={titleId}>자동 예매를 다시 시작할까요?</h2>
              </div>
              <button
                data-autofocus
                type="button"
                className="icon-button"
                aria-label="자동 예매 다시 시도 안내 닫기"
                disabled={pending}
                onClick={close}
              >
                <X size={22} />
              </button>
            </header>
            <div className="official-handoff-summary" aria-label="재시도 대상">
              <div><span>열차</span><strong>{trainLabel}</strong></div>
              <div><span>날짜</span><strong>{travelDate}</strong></div>
              <div><span>출발</span><strong>{departure}</strong></div>
              <div><span>좌석</span><strong>{seatClassLabel}</strong></div>
            </div>
            <div className="official-handoff-warning">
              <WarningCircle size={23} weight="fill" aria-hidden="true" />
              <div>
                <strong>
                  {mode.kind === "unknown"
                    ? "예약 결과를 자동으로 확인할 수 없습니다"
                    : mode.paymentHoldEndReason === "confirmed_payment_deadline_elapsed"
                      ? "공식 확인에서 결제 가능 기한이 종료됐습니다"
                      : "공식 내역에서 대상 임시 예약을 더 이상 찾지 못했습니다"}
                </strong>
                <span>
                  {mode.kind === "unknown"
                    ? "공식 앱/홈에서 해당 열차·좌석 등급의 예약이 없는지 직접 확인해 주세요."
                    : "공식 예약·승차권 내역에 결제할 예약이 남아 있는지 먼저 확인해 주세요."}
                </span>
              </div>
            </div>
            <p id={descriptionId} className="official-handoff-description">
              {mode.kind === "unknown"
                ? "확인하면 좌석 감시를 즉시 실행하고, 이후 같은 좌석 등급이 공식 관측에서 다시 예매 가능해질 때 한 번 자동 예매를 시도합니다. 자동 재확인은 계속되며, 그 사이 예약이 확인되면 재시도를 중단합니다. 현재 좌석 확보나 예매 성공을 보장하지 않으며 결제는 공식 플랫폼에서 직접 완료해야 합니다."
                : "확인하면 좌석 감시를 즉시 실행하고, 이후 같은 좌석 등급이 공식 관측에서 다시 예매 가능해질 때 한 번 자동 예매를 시도합니다. 현재 좌석 확보나 예매 성공을 보장하지 않으며 결제는 공식 플랫폼에서 직접 완료해야 합니다."}
            </p>
            <label className="reservation-rearm-acknowledgement">
              <input
                type="checkbox"
                checked={absenceConfirmed}
                disabled={pending}
                onChange={(event) => setAbsenceConfirmed(event.currentTarget.checked)}
              />
              <span>
                {mode.kind === "payment_hold"
                  && mode.paymentHoldEndReason === "confirmed_payment_deadline_elapsed"
                  ? "공식 앱/홈에서 이 예약을 더 이상 결제할 수 없음을 확인했습니다."
                  : "공식 앱/홈의 예약·승차권 내역에서 이 열차와 좌석 등급의 예약이 없음을 확인했습니다."}
              </span>
            </label>
            <footer className="official-handoff-actions">
              <button type="button" className="button button-outline" disabled={pending} onClick={close}>
                취소
              </button>
              <button
                type="button"
                className="button button-primary"
                aria-busy={pending}
                disabled={pending || !absenceConfirmed}
                onClick={() => { void confirm(); }}
              >
                <ArrowClockwise aria-hidden="true" />
                {pending ? "다시 시작 중…" : "확인하고 다시 시작"}
              </button>
            </footer>
          </section>
        </div>
      ), document.body)}
    </>
  );
}
