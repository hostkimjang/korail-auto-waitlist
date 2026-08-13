import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { ArrowsClockwise, Gauge, ShieldCheck } from "@phosphor-icons/react";

import {
  MAX_SEAT_OBSERVATION_INTERVAL_SECONDS,
  MIN_SEAT_OBSERVATION_INTERVAL_SECONDS,
  type UiPreferences,
  type UpdateUiPreferencesInput,
} from "../../api/uiPreferences";

export interface TimetableRefreshSettingsProps {
  preferences: UiPreferences;
  saving: boolean;
  onSave: (input: UpdateUiPreferencesInput) => Promise<UiPreferences>;
}

function toDraft(preferences: UiPreferences): string {
  return String(preferences.seatObservationIntervalSeconds);
}

function validateDraft(draft: string): { input?: UpdateUiPreferencesInput; error?: string } {
  const value = Number(draft);
  if (
    !Number.isInteger(value)
    || value < MIN_SEAT_OBSERVATION_INTERVAL_SECONDS
    || value > MAX_SEAT_OBSERVATION_INTERVAL_SECONDS
  ) {
    return {
      error: `좌석 관측 간격은 ${MIN_SEAT_OBSERVATION_INTERVAL_SECONDS}~${MAX_SEAT_OBSERVATION_INTERVAL_SECONDS}초 사이의 정수를 입력해 주세요.`,
    };
  }
  return { input: { seatObservationIntervalSeconds: value } };
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
      setSuccess("저장했습니다. 활성 작업의 다음 좌석 관측부터 새 간격이 적용됩니다.");
    } catch (saveError) {
      setDraft(toDraft(preferences));
      setError(saveError instanceof Error
        ? saveError.message
        : "좌석 관측 간격을 저장하지 못했습니다. 이전 설정을 유지합니다.");
    }
  };

  return (
    <form className="refresh-preference-card" aria-labelledby="refresh-preference-title" noValidate onSubmit={save}>
      <div className="refresh-preference-heading">
        <ArrowsClockwise size={28} weight="bold" aria-hidden="true" />
        <div>
          <strong id="refresh-preference-title">화면 동기화·좌석 관측</strong>
          <span>화면은 실시간으로 반영하고 좌석 관측 목표만 조정합니다.</span>
        </div>
      </div>

      <div className="refresh-preference-fields">
        <section className="refresh-preference-live" aria-labelledby="display-refresh-label">
          <span id="display-refresh-label">화면 표시 갱신</span>
          <small>서버 이벤트를 받으면 홈의 최신 상태를 바로 반영합니다.</small>
          <strong>실시간</strong>
          <small>이벤트를 놓치면 내부 자동 조회로 최신 상태를 복구합니다.</small>
        </section>
        <label>
          <span>좌석 관측 간격</span>
          <small>모든 활성 KORAIL·SRT 좌석 감시에 적용할 목표 간격입니다.</small>
          <span className="refresh-preference-input">
            <input
              type="number"
              min={MIN_SEAT_OBSERVATION_INTERVAL_SECONDS}
              max={MAX_SEAT_OBSERVATION_INTERVAL_SECONDS}
              step="1"
              inputMode="numeric"
              value={draft}
              disabled={saving}
              onChange={(event) => setDraft(event.target.value)}
              aria-describedby="observation-interval-safety"
            />
            <em>초</em>
          </span>
          <small>{MIN_SEAT_OBSERVATION_INTERVAL_SECONDS}~{MAX_SEAT_OBSERVATION_INTERVAL_SECONDS}초</small>
        </label>
      </div>

      <div id="observation-interval-safety" className="refresh-preference-safety">
        <ShieldCheck size={22} weight="fill" aria-hidden="true" />
        <p>
          <strong>입력값은 좌석 관측 목표 간격입니다.</strong>
          <span>저장하면 활성 작업의 다음 관측부터 즉시 적용됩니다. 실제 요청은 운영사별 단일 실행, provider lease, 캐시, 백오프, 쿨다운과 보호 정책에 따라 더 늦어질 수 있습니다.</span>
        </p>
      </div>

      <div className="refresh-preference-actions">
        <span><Gauge size={18} aria-hidden="true" />균형·집중 구분 없이 모든 활성 좌석 감시에 하나의 간격을 사용합니다.</span>
        <button type="submit" className="button button-primary compact" disabled={saving}>
          {saving ? "저장 중…" : "관측 간격 저장"}
        </button>
      </div>

      {error && <p className="refresh-preference-error" role="alert">{error}</p>}
      {success && <p className="refresh-preference-success" role="status">{success}</p>}
    </form>
  );
}
