import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import {
  ArrowLeft,
  ArrowRight,
  CalendarBlank,
  CaretDown,
} from "@phosphor-icons/react";

export type CalendarPickerProps = {
  value: string;
  onChange: (value: string) => void;
  label?: string;
  dialogLabel?: string;
};

type QuickDateKind = "today" | "tomorrow" | "weekend";

const DAY_IN_MILLISECONDS = 24 * 60 * 60 * 1_000;
const SATURDAY = 6;
const weekdayLabels = ["일", "월", "화", "수", "목", "금", "토"] as const;

function seoulDateInput(dayOffset = 0): string {
  const value = new Date(Date.now() + dayOffset * DAY_IN_MILLISECONDS);
  const parts = new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: "Asia/Seoul",
  }).formatToParts(value);
  const dateParts = new Map(parts.map((part) => [part.type, part.value]));
  const year = dateParts.get("year") ?? "";
  const month = dateParts.get("month") ?? "";
  const day = dateParts.get("day") ?? "";
  return `${year}-${month}-${day}`;
}

function parseDateInput(value: string): Date {
  const [yearValue, monthValue, dayValue] = value.split("-");
  const year = Number(yearValue);
  const month = Number(monthValue);
  const day = Number(dayValue);
  return new Date(year, month - 1, day);
}

function dateInputValue(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function dateLabel(value: string): string {
  return new Intl.DateTimeFormat("ko-KR", {
    month: "long",
    day: "numeric",
    weekday: "short",
  }).format(parseDateInput(value));
}

function nextWeekdayDate(baseValue: string, weekday: number): string {
  const today = seoulDateInput();
  const base = parseDateInput(baseValue < today ? today : baseValue);
  const distance = (weekday - base.getDay() + 7) % 7;
  base.setDate(base.getDate() + distance);
  return dateInputValue(base);
}

function calendarCells(viewDate: Date): ReadonlyArray<Date> {
  const monthStart = new Date(viewDate.getFullYear(), viewDate.getMonth(), 1);
  return Array.from({ length: 42 }, (_, index) => {
    const date = new Date(monthStart);
    date.setDate(index - monthStart.getDay() + 1);
    return date;
  });
}

export function CalendarPicker({
  value,
  onChange,
  label = "가는 날",
  dialogLabel,
}: CalendarPickerProps) {
  const [open, setOpen] = useState(false);
  const [viewDate, setViewDate] = useState(() => parseDateInput(value));
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const today = seoulDateInput();
  const resolvedDialogLabel = dialogLabel ?? `${label} 선택`;
  const cells = useMemo(() => calendarCells(viewDate), [viewDate]);

  useEffect(() => {
    if (!open) setViewDate(parseDateInput(value));
  }, [open, value]);

  const closeCalendar = (): void => {
    setOpen(false);
    window.setTimeout(() => triggerRef.current?.focus(), 0);
  };

  useEffect(() => {
    if (!open) return undefined;
    const focusTimer = window.setTimeout(() => {
      const dialog = dialogRef.current;
      const selectedDate = dialog?.querySelector<HTMLButtonElement>(
        'button[aria-pressed="true"]:not(:disabled)',
      );
      const firstButton = dialog?.querySelector<HTMLButtonElement>("button:not(:disabled)");
      (selectedDate ?? firstButton)?.focus();
    }, 0);
    return () => window.clearTimeout(focusTimer);
  }, [open]);

  const handleDialogKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeCalendar();
      return;
    }
    if (event.key !== "Tab") return;

    const dialog = dialogRef.current;
    if (!dialog) return;
    const focusable = Array.from(
      dialog.querySelectorAll<HTMLButtonElement>("button:not(:disabled)"),
    );
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (!first || !last) return;

    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const selectDate = (nextValue: string): void => {
    onChange(nextValue);
    setViewDate(parseDateInput(nextValue));
    closeCalendar();
  };

  const selectQuickDate = (kind: QuickDateKind): void => {
    if (kind === "today") {
      selectDate(today);
      return;
    }
    if (kind === "tomorrow") {
      selectDate(seoulDateInput(1));
      return;
    }
    selectDate(nextWeekdayDate(today, SATURDAY));
  };

  return (
    <div className="journey-field date-field">
      <span className="journey-label">
        <CalendarBlank size={18} aria-hidden="true" />
        {label}
      </span>
      <button
        ref={triggerRef}
        type="button"
        className="picker-trigger"
        aria-label={`${label}: ${dateLabel(value)}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        onKeyDown={(event) => {
          if (event.key === "Escape") closeCalendar();
        }}
        onClick={() => setOpen((current) => !current)}
      >
        <strong>{dateLabel(value)}</strong>
        <span>{value}</span>
        <CaretDown size={18} aria-hidden="true" />
      </button>
      {open && (
        <>
          <button
            type="button"
            className="popover-scrim"
            aria-label={`${resolvedDialogLabel} 닫기`}
            onClick={closeCalendar}
          />
          <div
            ref={dialogRef}
            className="journey-popover calendar-popover"
            role="dialog"
            aria-modal="true"
            aria-label={resolvedDialogLabel}
            onKeyDown={handleDialogKeyDown}
          >
            <div className="sheet-handle" aria-hidden="true" />
            <div className="calendar-topbar">
              <button
                type="button"
                aria-label="이전 달"
                onClick={() => setViewDate(new Date(viewDate.getFullYear(), viewDate.getMonth() - 1, 1))}
              >
                <ArrowLeft size={19} aria-hidden="true" />
              </button>
              <strong>{viewDate.getFullYear()}년 {viewDate.getMonth() + 1}월</strong>
              <button
                type="button"
                aria-label="다음 달"
                onClick={() => setViewDate(new Date(viewDate.getFullYear(), viewDate.getMonth() + 1, 1))}
              >
                <ArrowRight size={19} aria-hidden="true" />
              </button>
            </div>
            <div className="quick-chips" aria-label="빠른 날짜 선택">
              <button type="button" onClick={() => selectQuickDate("today")}>오늘</button>
              <button type="button" onClick={() => selectQuickDate("tomorrow")}>내일</button>
              <button type="button" onClick={() => selectQuickDate("weekend")}>이번 주말</button>
            </div>
            <div className="calendar-grid" aria-label={`${viewDate.getMonth() + 1}월 달력`}>
              {weekdayLabels.map((day) => (
                <span key={day} className="calendar-weekday">{day}</span>
              ))}
              {cells.map((date) => {
                const nextValue = dateInputValue(date);
                const outside = date.getMonth() !== viewDate.getMonth();
                const disabled = nextValue < today;
                return (
                  <button
                    key={nextValue}
                    type="button"
                    disabled={disabled}
                    aria-label={dateLabel(nextValue)}
                    aria-pressed={nextValue === value}
                    className={`${outside ? "is-outside " : ""}${nextValue === value ? "is-selected" : ""}`}
                    onClick={() => selectDate(nextValue)}
                  >
                    {date.getDate()}
                  </button>
                );
              })}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
