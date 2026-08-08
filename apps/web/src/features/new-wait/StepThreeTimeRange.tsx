import { useMemo, useState } from "react";
import {
  accessibleTimeBoundary,
  displayTimeRange,
  NEW_WAIT_TIME_PRESETS,
  SERVICE_DATE_END_TIME,
  type NewWaitTimePreset,
} from "./timeRange";

type TimeOption = {
  value: string;
  label: string;
};

type StepThreeTimeRangeProps = {
  appliedStart: string;
  appliedEnd: string;
  busy: boolean;
  onApply: (start: string, end: string) => void;
};

const sameDayTimeOptions: TimeOption[] = Array.from({ length: 48 }, (_, index) => {
  const hour = String(Math.floor(index / 2)).padStart(2, "0");
  const minute = index % 2 === 0 ? "00" : "30";
  const value = `${hour}:${minute}`;
  return { value, label: value };
});

// The timetable API accepts one Korea service date and requires start < end.
// Represent the next midnight as the final minute of that service date rather
// than sending same-date 00:00, which would invert the requested time window.
const serviceDateEndOption: TimeOption = {
  value: SERVICE_DATE_END_TIME,
  label: "다음 날 00:00",
};

export function StepThreeTimeRange(props: StepThreeTimeRangeProps) {
  return (
    <StepThreeTimeRangeDraft
      key={`${props.appliedStart}:${props.appliedEnd}`}
      {...props}
    />
  );
}

function StepThreeTimeRangeDraft({
  appliedStart,
  appliedEnd,
  busy,
  onApply,
}: StepThreeTimeRangeProps) {
  const [draftStart, setDraftStart] = useState(appliedStart);
  const [draftEnd, setDraftEnd] = useState(appliedEnd);

  const dirty = draftStart !== appliedStart || draftEnd !== appliedEnd;
  const invalid = draftStart >= draftEnd;
  const status = useMemo(() => {
    if (busy) return `${displayTimeRange(appliedStart, appliedEnd)} 시간표 조회 중`;
    if (invalid) return "종료 시간을 시작 시간보다 늦게 선택해 주세요.";
    if (dirty) return "변경한 시간 범위를 적용해 다시 조회할 수 있습니다.";
    return `${displayTimeRange(appliedStart, appliedEnd)} 조회 완료`;
  }, [appliedEnd, appliedStart, busy, dirty, invalid]);

  const choosePreset = (preset: NewWaitTimePreset) => {
    setDraftStart(preset.start);
    setDraftEnd(preset.end);
  };

  return (
    <fieldset className="step-three-time-range" aria-busy={busy}>
      <legend>출발 시간 다시 조회</legend>
      <div className="step-three-time-presets" aria-label="출발 시간대 빠른 선택">
        {NEW_WAIT_TIME_PRESETS.map((preset) => (
          <button
            key={preset.label}
            type="button"
            aria-label={`${preset.label} ${preset.start}부터 ${accessibleTimeBoundary(preset.end)}까지`}
            aria-pressed={draftStart === preset.start && draftEnd === preset.end}
            onClick={() => choosePreset(preset)}
          >
            {preset.label}
          </button>
        ))}
      </div>
      <div className="step-three-time-controls">
        <label>
          <span>시작</span>
          <select aria-label="재조회 시작 시간" value={draftStart} onChange={(event) => setDraftStart(event.target.value)}>
            {sameDayTimeOptions.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </label>
        <span aria-hidden="true">–</span>
        <label>
          <span>종료</span>
          <select aria-label="재조회 종료 시간" value={draftEnd} onChange={(event) => setDraftEnd(event.target.value)}>
            {[...sameDayTimeOptions.slice(1), serviceDateEndOption].map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="button button-primary compact"
          disabled={!dirty || invalid}
          onClick={() => onApply(draftStart, draftEnd)}
        >
          {busy ? "범위 변경" : "적용·재조회"}
        </button>
      </div>
      <p className={invalid ? "is-error" : ""} role="status" aria-live="polite">{status}</p>
    </fieldset>
  );
}
