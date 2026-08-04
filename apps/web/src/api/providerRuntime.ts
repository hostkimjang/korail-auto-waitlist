import type { ProviderAuthStatus, RailProvider } from "./providerAccounts";

export type ProviderRuntimeState =
  | "cold"
  | "authenticating"
  | "ready"
  | "stale"
  | "auth_required"
  | "blocked";

export interface ProviderRuntimeStatus {
  provider: RailProvider;
  state: ProviderRuntimeState;
  credentialGeneration: string | null;
  createdAgeSeconds: number | null;
  lastVerifiedAgeSeconds: number | null;
  lastUsedAgeSeconds: number | null;
  localReuseRemainingSeconds: number | null;
  locallyReusable: boolean;
  prewarmOutcome: ProviderAuthStatus | null;
}

const providers: ReadonlySet<string> = new Set(["KORAIL", "SRT"]);
const runtimeStates: ReadonlySet<string> = new Set([
  "cold",
  "authenticating",
  "ready",
  "stale",
  "auth_required",
  "blocked",
]);
const prewarmOutcomes: ReadonlySet<string> = new Set([
  "not_checked",
  "authenticated",
  "auth_required",
  "provider_blocked",
  "failed",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isNullableNonNegativeNumber(value: unknown): value is number | null {
  return value === null || (typeof value === "number" && Number.isFinite(value) && value >= 0);
}

function isRuntimeState(value: unknown): value is ProviderRuntimeState {
  return typeof value === "string" && runtimeStates.has(value);
}

function isPrewarmOutcome(value: unknown): value is ProviderAuthStatus | null {
  return value === null || (typeof value === "string" && prewarmOutcomes.has(value));
}

function normalizeProvider(value: unknown): RailProvider | null {
  if (typeof value !== "string") return null;
  const provider = value.toUpperCase();
  if (!providers.has(provider)) return null;
  return provider === "KORAIL" ? "KORAIL" : "SRT";
}

export function mapProviderRuntimeStatus(value: unknown): ProviderRuntimeStatus {
  if (!isRecord(value)) {
    throw new Error("상주 세션 상태 응답 형식을 확인할 수 없습니다.");
  }

  const provider = normalizeProvider(value.provider);
  const credentialGeneration = value.credential_generation;
  const createdAgeSeconds = value.created_age_seconds;
  const lastVerifiedAgeSeconds = value.last_verified_age_seconds;
  const lastUsedAgeSeconds = value.last_used_age_seconds;
  const localReuseRemainingSeconds = value.local_reuse_remaining_seconds;
  const prewarmOutcome = value.prewarm_outcome;
  if (
    provider === null
    || !isRuntimeState(value.state)
    || !isNullableString(credentialGeneration)
    || !isNullableNonNegativeNumber(createdAgeSeconds)
    || !isNullableNonNegativeNumber(lastVerifiedAgeSeconds)
    || !isNullableNonNegativeNumber(lastUsedAgeSeconds)
    || !isNullableNonNegativeNumber(localReuseRemainingSeconds)
    || typeof value.locally_reusable !== "boolean"
    || !isPrewarmOutcome(prewarmOutcome)
  ) {
    throw new Error("상주 세션 상태 응답 형식을 확인할 수 없습니다.");
  }

  return {
    provider,
    state: value.state,
    credentialGeneration,
    createdAgeSeconds,
    lastVerifiedAgeSeconds,
    lastUsedAgeSeconds,
    localReuseRemainingSeconds,
    locallyReusable: value.locally_reusable,
    prewarmOutcome,
  };
}

export function mapProviderRuntimeStatuses(value: unknown): ProviderRuntimeStatus[] {
  if (!Array.isArray(value)) {
    throw new Error("상주 세션 상태 목록을 확인할 수 없습니다.");
  }

  const byProvider = new Map<RailProvider, ProviderRuntimeStatus>();
  for (const item of value) {
    const status = mapProviderRuntimeStatus(item);
    if (byProvider.has(status.provider)) {
      throw new Error("상주 세션 상태에 중복된 운영사가 있습니다.");
    }
    byProvider.set(status.provider, status);
  }

  const korail = byProvider.get("KORAIL");
  const srt = byProvider.get("SRT");
  if (byProvider.size !== 2 || !korail || !srt) {
    throw new Error("상주 세션 상태에 KORAIL 또는 SRT 정보가 없습니다.");
  }
  return [korail, srt];
}

export async function fetchProviderRuntimeStatuses(): Promise<ProviderRuntimeStatus[]> {
  const response = await fetch("/api/v1/provider-runtime-status", {
    headers: { Accept: "application/json" },
    cache: "no-store",
    credentials: "include",
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error("상주 세션 상태를 불러오지 못했습니다.");
  }
  return mapProviderRuntimeStatuses(payload);
}
