import { useCallback, useEffect, useState } from "react";

import {
  DEFAULT_SEAT_OBSERVATION_INTERVAL_SECONDS,
  fetchUiPreferences,
  updateUiPreferences,
  type UiPreferences,
  type UpdateUiPreferencesInput,
} from "../../api/uiPreferences";

export type UiPreferencesLoader = () => Promise<UiPreferences>;
export type UiPreferencesPersister = (
  input: UpdateUiPreferencesInput,
) => Promise<UiPreferences>;

export interface UseUiPreferencesSettingsOptions {
  authenticated: boolean;
  demo: boolean;
  pushToast: (message: string) => void;
  loadPreferences?: UiPreferencesLoader;
  persistPreferences?: UiPreferencesPersister;
  now?: () => string;
}

export interface UiPreferencesSettingsController {
  preferences: UiPreferences;
  saving: boolean;
  save: (input: UpdateUiPreferencesInput) => Promise<UiPreferences>;
  reset: () => void;
}

function currentIsoTimestamp(): string {
  return new Date().toISOString();
}

function defaultUiPreferences(): UiPreferences {
  return {
    seatObservationIntervalSeconds: DEFAULT_SEAT_OBSERVATION_INTERVAL_SECONDS,
    updatedAt: new Date(0).toISOString(),
  };
}

function errorMessage(reason: unknown, fallback: string): string {
  return reason instanceof Error && reason.message ? reason.message : fallback;
}

export function useUiPreferencesSettings({
  authenticated,
  demo,
  pushToast,
  loadPreferences = fetchUiPreferences,
  persistPreferences = updateUiPreferences,
  now = currentIsoTimestamp,
}: UseUiPreferencesSettingsOptions): UiPreferencesSettingsController {
  const [preferences, setPreferences] = useState<UiPreferences>(defaultUiPreferences);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!authenticated || demo) return undefined;
    let active = true;
    void loadPreferences().then((loaded) => {
      if (active) setPreferences(loaded);
    }).catch((reason: unknown) => {
      if (active) {
        pushToast(errorMessage(reason, "화면 동작 설정을 불러오지 못했습니다."));
      }
    });
    return () => {
      active = false;
    };
  }, [authenticated, demo, loadPreferences, pushToast]);

  const save = useCallback(async (
    input: UpdateUiPreferencesInput,
  ): Promise<UiPreferences> => {
    setSaving(true);
    try {
      const saved = demo
        ? { ...input, updatedAt: now() }
        : await persistPreferences(input);
      setPreferences(saved);
      pushToast("좌석 관측 간격을 저장했습니다. 활성 작업의 다음 관측부터 적용됩니다.");
      return saved;
    } catch (reason: unknown) {
      pushToast(errorMessage(reason, "좌석 관측 간격을 저장하지 못했습니다."));
      throw reason;
    } finally {
      setSaving(false);
    }
  }, [demo, now, persistPreferences, pushToast]);

  const reset = useCallback((): void => {
    setPreferences(defaultUiPreferences());
    setSaving(false);
  }, []);

  return { preferences, saving, save, reset };
}
