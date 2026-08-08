import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ArrowSquareOut, CheckCircle, X } from "@phosphor-icons/react";

import { useDocumentScrollLock } from "../../hooks/useDocumentScrollLock";
import {
  saveOfficialSeatConfirmation,
  type ConfirmableSeatClass,
  type OfficialProvider,
  type OfficialSeatConfirmationResult,
  type OfficialSeatConfirmationStatus,
} from "./officialSeatConfirmationApi";

type ConfirmationChoice = OfficialSeatConfirmationStatus | "unknown";
type SubmitState = "idle" | "pending" | "error" | "success";

interface OfficialSeatTrain {
  id: string;
  provider: OfficialProvider;
  name: string;
  train_number: string;
  origin: string;
  destination: string;
  departure: string;
  arrival: string;
  departure_at: string;
}

interface OfficialSeatConfirmationProps {
  train: OfficialSeatTrain;
  originNodeId: string;
  destinationNodeId: string;
  passengerCount: number;
  officialUrl: string | null;
  onSaved: (result: OfficialSeatConfirmationResult) => Promise<void> | void;
}

const seatClassLabels: Record<ConfirmableSeatClass, string> = {
  standard: "일반실",
  first: "특실",
};

const choices: { value: ConfirmationChoice; label: string }[] = [
  { value: "unknown", label: "선택 안 함" },
  { value: "available", label: "예매 가능" },
  { value: "sold_out", label: "매진" },
  { value: "waitlist_available", label: "예약대기 가능" },
  { value: "not_offered", label: "좌석 등급 없음" },
];

function choiceFromValue(value: string): ConfirmationChoice {
  if (
    value === "available"
    || value === "sold_out"
    || value === "waitlist_available"
    || value === "not_offered"
  ) return value;
  return "unknown";
}

function observedTimeLabel(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "방금";
  return new Intl.DateTimeFormat("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Seoul",
  }).format(parsed);
}

export function OfficialSeatConfirmation({
  train,
  originNodeId,
  destinationNodeId,
  passengerCount,
  officialUrl,
  onSaved,
}: OfficialSeatConfirmationProps) {
  const [open, setOpen] = useState(false);
  const [standard, setStandard] = useState<ConfirmationChoice>("unknown");
  const [first, setFirst] = useState<ConfirmationChoice>("unknown");
  const [submitState, setSubmitState] = useState<SubmitState>("idle");
  const [message, setMessage] = useState("");
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const idempotencyKeyRef = useRef("");
  const titleId = `${useId()}-title`;
  const descriptionId = `${useId()}-description`;
  const hasSelection = standard !== "unknown" || first !== "unknown";
  const pending = submitState === "pending";
  useDocumentScrollLock(open);

  const resetForm = () => {
    setStandard("unknown");
    setFirst("unknown");
    setSubmitState("idle");
    setMessage("");
    idempotencyKeyRef.current = "";
  };

  const close = () => {
    if (pending) return;
    setOpen(false);
    window.setTimeout(() => triggerRef.current?.focus(), 0);
  };

  useEffect(() => {
    if (!open) return undefined;
    const appRoot = triggerRef.current?.closest<HTMLElement>(".app-shell") ?? null;
    if (appRoot) {
      appRoot.inert = true;
      appRoot.setAttribute("aria-hidden", "true");
    }
    const focusTimer = window.setTimeout(() => {
      dialogRef.current?.querySelector<HTMLElement>("[data-autofocus]")?.focus();
    }, 0);
    return () => {
      window.clearTimeout(focusTimer);
      if (appRoot) {
        appRoot.inert = false;
        appRoot.removeAttribute("aria-hidden");
      }
    };
  }, [open]);

  const updateChoice = (seatClass: ConfirmableSeatClass, value: string) => {
    const choice = choiceFromValue(value);
    if (seatClass === "standard") setStandard(choice);
    else setFirst(choice);
    if (submitState === "error") {
      setSubmitState("idle");
      setMessage("");
    }
    idempotencyKeyRef.current = "";
  };

  const save = async () => {
    if (!hasSelection || pending || submitState === "success") return;
    const seatClasses = ([
      ["standard", standard],
      ["first", first],
    ] as const).flatMap(([seatClass, status]) => status === "unknown" ? [] : [{ seat_class: seatClass, status }]);
    if (!seatClasses.length) return;
    if (!idempotencyKeyRef.current) idempotencyKeyRef.current = crypto.randomUUID();
    setSubmitState("pending");
    setMessage("");
    try {
      const result = await saveOfficialSeatConfirmation({
        provider: train.provider,
        origin_node_id: originNodeId,
        destination_node_id: destinationNodeId,
        train_number: train.train_number,
        departure_at: train.departure_at,
        passenger_count: passengerCount,
        seat_classes: seatClasses,
      }, idempotencyKeyRef.current);
      setSubmitState("success");
      setMessage(`공식 페이지에서 확인한 좌석 상태를 ${observedTimeLabel(result.observedAt)} 기준으로 저장했습니다.`);
      try {
        await onSaved(result);
        setMessage(`공식 페이지에서 확인한 좌석 상태를 ${observedTimeLabel(result.observedAt)} 기준으로 저장하고 목록을 다시 조회했습니다.`);
      } catch {
        setMessage(`공식 페이지에서 확인한 좌석 상태는 저장했지만 목록을 다시 불러오지 못했습니다.`);
      }
    } catch (error) {
      setSubmitState("error");
      setMessage(error instanceof Error ? error.message : "공식 좌석 확인 결과를 저장하지 못했습니다.");
    }
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = [...(dialogRef.current?.querySelectorAll<HTMLElement>(
      'button:not(:disabled), [href], select:not(:disabled), [tabindex]:not([tabindex="-1"])',
    ) ?? [])];
    if (!focusable.length) return;
    const firstFocusable = focusable[0];
    const lastFocusable = focusable[focusable.length - 1];
    if (!firstFocusable || !lastFocusable) return;
    if (event.shiftKey && document.activeElement === firstFocusable) {
      event.preventDefault();
      lastFocusable.focus();
    } else if (!event.shiftKey && document.activeElement === lastFocusable) {
      event.preventDefault();
      firstFocusable.focus();
    }
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className="official-confirmation-trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`${train.name} 공식 페이지에서 확인한 좌석 상태 입력`}
        onClick={() => {
          resetForm();
          setOpen(true);
        }}
      >
        좌석 상태 입력
      </button>
      {open && createPortal((
        <div className="official-confirmation-layer">
          <div className="official-confirmation-scrim" aria-hidden="true" onClick={close} />
          <section
            ref={dialogRef}
            className="official-confirmation-panel"
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            aria-describedby={descriptionId}
            aria-busy={pending}
            onKeyDown={handleKeyDown}
          >
            <header className="official-confirmation-header">
              <div>
                <span className={`provider-chip ${train.provider === "SRT" ? "provider-srt" : "provider-korail"}`}>{train.provider}</span>
                <h2 id={titleId}>공식 좌석 상태 입력</h2>
              </div>
              <button data-autofocus type="button" className="icon-button" aria-label="공식 좌석 상태 입력 닫기" disabled={pending} onClick={close}><X size={22} /></button>
            </header>

            <div className="official-confirmation-summary" aria-label="변경할 수 없는 열차 정보">
              <div><span>여정</span><strong>{train.origin} → {train.destination}</strong></div>
              <div><span>열차</span><strong>{train.name}</strong></div>
              <div><span>출발</span><strong>{train.departure} → {train.arrival}</strong></div>
              <div><span>인원</span><strong>성인 {passengerCount}명</strong></div>
            </div>

            <p id={descriptionId} className="official-confirmation-description">
              공식 페이지에서 이 열차와 인원을 직접 확인한 뒤, 화면에 표시된 상태만 선택하세요. 선택하지 않은 좌석 등급은 저장하지 않습니다.
            </p>
            {officialUrl && <a className="button button-outline official-confirmation-link" href={officialUrl} target="_blank" rel="noopener noreferrer">공식 페이지 열기 <ArrowSquareOut size={18} aria-hidden="true" /></a>}

            <div className="official-confirmation-fields">
              {(["standard", "first"] as const).map((seatClass) => {
                const value = seatClass === "standard" ? standard : first;
                return (
                  <label key={seatClass} className="official-confirmation-field">
                    <span>{seatClassLabels[seatClass]}</span>
                    <select
                      value={value}
                      disabled={pending || submitState === "success"}
                      aria-label={`${seatClassLabels[seatClass]} 공식 페이지 확인 결과`}
                      onChange={(event) => updateChoice(seatClass, event.currentTarget.value)}
                    >
                      {choices.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
                    </select>
                  </label>
                );
              })}
            </div>

            {!hasSelection && submitState !== "success" && <p className="official-confirmation-hint">일반실 또는 특실 중 하나 이상을 선택해 주세요.</p>}
            {message && <div className={submitState === "error" ? "official-confirmation-error" : "official-confirmation-success"} role={submitState === "error" ? "alert" : "status"}>
              {submitState === "success" && <CheckCircle size={20} weight="fill" aria-hidden="true" />}
              <span>{message}</span>
            </div>}

            <footer className="official-confirmation-actions">
              <button type="button" className="button button-ghost" disabled={pending} onClick={close}>{submitState === "success" ? "닫기" : "취소"}</button>
              <button type="button" className="button button-primary" aria-busy={pending} disabled={!hasSelection || pending || submitState === "success"} onClick={save}>
                {pending ? "저장 중…" : submitState === "success" ? "저장 완료" : "확인 결과 저장"}
              </button>
            </footer>
          </section>
        </div>
      ), document.body)}
    </>
  );
}
