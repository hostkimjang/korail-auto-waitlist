import { CheckCircle, Circle, SpinnerGap, WarningCircle, X, XCircle } from "@phosphor-icons/react";
import { useEffect, useRef } from "react";

export const TOAST_AUTO_CLOSE_MS = 30_000;
export const IMPORTANT_TOAST_AUTO_CLOSE_MS = 60_000;

export type ToastTone = "info" | "success" | "warning" | "error";
export type ToastProgressState = "completed" | "active" | "pending" | "failed";

export interface ToastProgressStep {
  label: string;
  state: ToastProgressState;
  occurredAt?: string | null;
  durationMs?: number | null;
  durationPrefix?: "대기" | "처리" | "소요" | "감지 후" | "이전 단계 후";
  showNoticeDuration?: boolean;
}

export interface AppToastInput {
  key?: string;
  title: string;
  meta?: string;
  description?: string;
  tone?: ToastTone;
  steps?: ReadonlyArray<ToastProgressStep>;
  autoCloseMs?: number | null;
  occurredAt?: string | null;
  startedAt?: string | null;
  durationMs?: number | null;
}

export interface AppToastNotice extends AppToastInput {
  id: string;
}

interface AppToastProps {
  notice: AppToastNotice;
  onClose: () => void;
  embedded?: boolean;
}

function StepIcon({ state }: { state: ToastProgressState }) {
  if (state === "completed") return <CheckCircle size={15} weight="fill" aria-hidden="true" />;
  if (state === "active") return <SpinnerGap className="toast-step-spinner" size={15} aria-hidden="true" />;
  if (state === "failed") return <XCircle size={15} weight="fill" aria-hidden="true" />;
  return <Circle size={15} aria-hidden="true" />;
}

const koreaTimeFormatter = new Intl.DateTimeFormat("ko-KR", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
  timeZone: "Asia/Seoul",
});

function notificationTime(value: string | null | undefined): string | null {
  if (!value) return null;
  const instant = new Date(value);
  return Number.isNaN(instant.getTime()) ? null : koreaTimeFormatter.format(instant);
}

function durationLabel(value: number | null | undefined): string | null {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return null;
  if (value < 60_000) return `${(value / 1_000).toFixed(1)}초`;
  const totalSeconds = Math.round(value / 1_000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}분 ${seconds}초`;
}

export function AppToast({ notice, onClose, embedded = false }: AppToastProps) {
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const duration = notice.autoCloseMs === undefined
      ? notice.steps?.length
        ? IMPORTANT_TOAST_AUTO_CLOSE_MS
        : TOAST_AUTO_CLOSE_MS
      : notice.autoCloseMs;
    if (duration === null) return undefined;
    const timer = window.setTimeout(() => onCloseRef.current(), duration);
    return () => window.clearTimeout(timer);
  }, [notice.id, notice.autoCloseMs, notice.steps]);

  const tone = notice.tone ?? "info";
  const occurredAtLabel = notificationTime(notice.occurredAt);
  const startedAtLabel = notificationTime(notice.startedAt);
  const elapsedLabel = durationLabel(notice.durationMs);
  const hasStepTiming = notice.steps?.some((step) => notificationTime(step.occurredAt) !== null)
    ?? false;
  const leadingIcon = tone === "error" || tone === "warning"
    ? <WarningCircle size={24} weight="fill" aria-hidden="true" />
    : <CheckCircle size={24} weight="fill" aria-hidden="true" />;

  return (
    <section
      className={`${embedded ? "notification-card" : "toast"} toast-${tone}`}
      role={embedded ? undefined : tone === "error" ? "alert" : "status"}
    >
      <div className="toast-leading-icon">{leadingIcon}</div>
      <div className="toast-content">
        <div className="toast-title-row">
          <strong>{notice.title}</strong>
          {occurredAtLabel && notice.occurredAt ? (
            <time
              className="toast-timestamp"
              dateTime={notice.occurredAt}
              aria-label={`알림 발생 시각 ${occurredAtLabel}`}
            >
              {occurredAtLabel}
            </time>
          ) : null}
        </div>
        {notice.meta && <span className="toast-meta">{notice.meta}</span>}
        {notice.description && <span className="toast-description">{notice.description}</span>}
        {notice.steps && notice.steps.length > 0 && (
          <>
            <ol className="toast-steps" aria-label="예매 진행 단계">
              {notice.steps.map((step) => {
                const stepOccurredAtLabel = notificationTime(step.occurredAt);
                const stepDurationLabel = durationLabel(
                  step.durationMs ?? (step.showNoticeDuration ? notice.durationMs : null),
                );
                return (
                <li key={step.label} className={`toast-step toast-step-${step.state}`}>
                  <span className="toast-step-label">
                    <StepIcon state={step.state} />
                    <span>{step.label}</span>
                  </span>
                  {stepOccurredAtLabel && step.occurredAt ? (
                    <small className="toast-step-timing">
                      <time dateTime={step.occurredAt}>{stepOccurredAtLabel}</time>
                      {stepDurationLabel ? (
                        <span>{step.durationPrefix ?? "소요"} {stepDurationLabel}</span>
                      ) : null}
                    </small>
                  ) : null}
                </li>
                );
              })}
            </ol>
            {startedAtLabel && notice.startedAt && (!hasStepTiming || elapsedLabel) ? (
              <div className="toast-timing" aria-label="예매 작업 시간">
                <span>
                  시작 <time dateTime={notice.startedAt}>{startedAtLabel}</time>
                </span>
                <span>{elapsedLabel ? `전체 ${elapsedLabel}` : "처리 중"}</span>
              </div>
            ) : null}
          </>
        )}
      </div>
      <button
        type="button"
        className="toast-close"
        aria-label={embedded ? `${notice.title} 알림 닫기` : "알림 닫기"}
        onClick={onClose}
      >
        <X size={20} aria-hidden="true" />
      </button>
    </section>
  );
}
