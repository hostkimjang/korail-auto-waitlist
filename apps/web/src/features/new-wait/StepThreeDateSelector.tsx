import { CalendarPicker } from "./CalendarPicker";

export type StepThreeDateSelectorProps = {
  value: string;
  appliedDateLabel: string;
  busy: boolean;
  onChange: (value: string) => void;
};

export function StepThreeDateSelector({
  value,
  appliedDateLabel,
  busy,
  onChange,
}: StepThreeDateSelectorProps) {
  return (
    <fieldset className="step-three-date-selector" aria-busy={busy}>
      <legend>
        출발일 변경
        <span>달력에서 날짜를 선택하면 해당 날짜의 시간표를 다시 조회합니다</span>
      </legend>
      <CalendarPicker
        value={value}
        onChange={onChange}
        label="출발일"
        dialogLabel="시간표 출발일 선택"
      />
      <p role="status" aria-live="polite">
        {busy ? `${appliedDateLabel} 시간표 조회 중` : `${appliedDateLabel} 출발일 적용됨`}
      </p>
    </fieldset>
  );
}
