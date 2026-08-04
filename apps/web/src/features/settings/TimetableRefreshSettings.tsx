import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { ArrowsClockwise, Gauge, ShieldCheck } from "@phosphor-icons/react";

import {
  MAX_SEAT_OBSERVATION_INTERVAL_SECONDS,
  MAX_TIMETABLE_REFRESH_INTERVAL_SECONDS,
  MIN_SEAT_OBSERVATION_INTERVAL_SECONDS,
  MIN_TIMETABLE_REFRESH_INTERVAL_SECONDS,
  type UiPreferences,
  type UpdateUiPreferencesInput,
} from "../../api/uiPreferences";

export interface TimetableRefreshSettingsProps {
  preferences: UiPreferences;
  saving: boolean;
  onSave: (input: UpdateUiPreferencesInput) => Promise<UiPreferences>;
}

interface IntervalField {
  key: keyof UpdateUiPreferencesInput;
  label: string;
  helper: string;
  min: number;
  max: number;
}

const intervalFields: IntervalField[] = [
  {
    key: "timetableRefreshIntervalSeconds",
    label: "화면 표시 갱신",
    helper: "새 대기와 홈이 서버에 저장된 결과를 다시 읽습니다.",
    min: MIN_TIMETABLE_REFRESH_INTERVAL_SECONDS,
    max: MAX_TIMETABLE_REFRESH_INTERVAL_SECONDS,
  },
  {
    key: "seatObservationIntervalSeconds",
    label: "좌석 관측 간격",
    helper: "모든 활성 KORAIL·SRT 좌석 감시에 적용할 목표 간격입니다.",
    min: MIN_SEAT_OBSERVATION_INTERVAL_SECONDS,
    max: MAX_SEAT_OBSERVATION_INTERVAL_SECONDS,
  },
];

function toDraft(preferences: UiPreferences): Record<keyof UpdateUiPreferencesInput, string> {
  return {
    timetableRefreshIntervalSeconds: String(preferences.timetableRefreshIntervalSeconds),
    seatObservationIntervalSeconds: String(preferences.seatObservationIntervalSeconds),
  };
}

function validateDraft(
  draft: Record<keyof UpdateUiPreferencesInput, string>,
): { input?: UpdateUiPreferencesInput; error?: string } {
  const values = Object.fromEntries(
    intervalFields.map((field) => [field.key, Number(draft[field.key])]),
  ) as Record<keyof UpdateUiPreferencesInput, number>;
  const invalid = intervalFields.find((field) => (
    !Number.isInteger(values[field.key])
    || values[field.key] < field.min
    || values[field.key] > field.max
  ));
  if (invalid) {
    return { error: `${invalid.label}은 ${invalid.min}~${invalid.max}초 사이의 정수를 입력해 주세요.` };
  }
  return { input: values };
}

export function TimetableRefreshSettings({
  preferences,
  saving,
  onSave,
}: TimetableRefreshSettingsProps) {
  const [draft, setDraft] = useState(() => toDraft(preferences));
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  useEffect(() => setDraft(toDraft(preferences)), [preferences]);

  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (saving) return;
    const validation = validateDraft(draft);
    if (!validation.input) {
      setSuccess("");
      setError(validation.error ?? "입력값을 확인해 주세요.");
      return;
    }
    setError("");
    setSuccess("");
    try {
      const saved = await onSave(validation.input);
      setDraft(toDraft(saved));
      setSuccess("저장했습니다. 활성 작업의 다음 관측부터 새 간격이 적용됩니다.");
    } catch (saveError) {
      setDraft(toDraft(preferences));
      setError(saveError instanceof Error
        ? saveError.message
        : "화면·관측 간격을 저장하지 못했습니다. 이전 설정을 유지합니다.");
    }
  };

  return (
    <form className="refresh-preference-card" aria-labelledby="refresh-preference-title" noValidate onSubmit={save}>
      <div className="refresh-preference-heading">
        <ArrowsClockwise size={28} weight="bold" aria-hidden="true" />
        <div>
          <strong id="refresh-preference-title">화면·좌석 관측 간격</strong>
          <span>화면 표시와 백엔드 좌석 관측 목표를 조정합니다.</span>
        </div>
      </div>

      <div className="refresh-preference-fields">
        {intervalFields.map((field) => (
          <label key={field.key}>
            <span>{field.label}</span>
            <small>{field.helper}</small>
            <span className="refresh-preference-input">
              <input
                type="number"
                min={field.min}
                max={field.max}
                step="1"
                inputMode="numeric"
                value={draft[field.key]}
                disabled={saving}
                onChange={(event) => setDraft((current) => ({
                  ...current,
                  [field.key]: event.target.value,
                }))}
                aria-describedby="observation-interval-safety"
              />
              <em>초</em>
            </span>
            <small>{field.min}~{field.max}초</small>
          </label>
        ))}
      </div>

      <div id="observation-interval-safety" className="refresh-preference-safety">
        <ShieldCheck size={22} weight="fill" aria-hidden="true" />
        <p>
          <strong>입력값은 목표 간격입니다.</strong>
          <span>저장하면 활성 작업의 다음 관측부터 즉시 적용됩니다. 실제 요청은 운영사별 단일 실행, provider lease, 캐시, 백오프, 쿨다운과 보호 정책에 따라 더 늦어질 수 있습니다.</span>
        </p>
      </div>

      <div className="refresh-preference-actions">
        <span><Gauge size={18} aria-hidden="true" />균형·집중 구분 없이 모든 활성 좌석 감시에 하나의 간격을 사용합니다.</span>
        <button type="submit" className="button button-primary compact" disabled={saving}>
          {saving ? "저장 중…" : "간격 저장"}
        </button>
      </div>

      {error && <p className="refresh-preference-error" role="alert">{error}</p>}
      {success && <p className="refresh-preference-success" role="status">{success}</p>}
    </form>
  );
}
