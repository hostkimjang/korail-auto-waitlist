/**
 * Settings the web container writes at start from the install's .env (apps/web/docker/start.sh).
 * They override the values compiled into the bundle, so an official release image follows .env
 * without a rebuild. A key that is missing, not a string or longer than 64 characters is dropped
 * and falls back to the build-time value.
 */
export const RUNTIME_SETTING_KEYS = [
  "VITE_KORAIL_BOOKING_DEEPLINK_ENABLED",
  "VITE_KORAIL_BOOKING_VALIDATED_VERSION",
  "VITE_KORAIL_TICKET_DEEPLINK_ENABLED",
  "VITE_KORAIL_TICKET_VALIDATED_VERSION",
  "VITE_SRT_MAIN_DEEPLINK_ENABLED",
  "VITE_SRT_MAIN_VALIDATED_VERSION",
  "VITE_SRT_TICKET_DEEPLINK_ENABLED",
  "VITE_SRT_TICKET_VALIDATED_VERSION",
] as const;

export type RuntimeSettingKey = (typeof RUNTIME_SETTING_KEYS)[number];
export type RuntimeSettings = Partial<Record<RuntimeSettingKey, string>>;

declare global {
  interface Window {
    __RAILWAIT_RUNTIME_CONFIG__?: unknown;
  }
}

export function parseRuntimeSettings(source: unknown): RuntimeSettings {
  if (typeof source !== "object" || source === null || Array.isArray(source)) return {};
  const settings: RuntimeSettings = {};
  for (const key of RUNTIME_SETTING_KEYS) {
    const value: unknown = Reflect.get(source, key);
    if (typeof value === "string" && value.length <= 64) settings[key] = value;
  }
  return settings;
}

export function runtimeSettings(): RuntimeSettings {
  return typeof window === "undefined" ? {} : parseRuntimeSettings(window.__RAILWAIT_RUNTIME_CONFIG__);
}
