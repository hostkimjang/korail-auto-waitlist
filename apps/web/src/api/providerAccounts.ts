export type RailProvider = "KORAIL" | "SRT";

export type ProviderLoginMethod = "membership_number" | "email" | "phone";

export type ProviderAuthStatus =
  | "not_checked"
  | "authenticated"
  | "auth_required"
  | "provider_blocked"
  | "failed";

export interface ProviderAccount {
  provider: RailProvider;
  configured: boolean;
  enabled: boolean;
  loginMethod: ProviderLoginMethod | null;
  maskedLoginId: string | null;
  credentialVersion: number;
  lastAuthStatus: ProviderAuthStatus;
  lastAuthenticatedAt: string | null;
  updatedAt: string | null;
}

export interface ProviderAccountCredentialInput {
  loginMethod: ProviderLoginMethod;
  loginId: string;
  password: string;
  enabled?: boolean;
}

interface ProviderAccountDto {
  provider: string;
  configured: boolean;
  enabled: boolean;
  login_method: string | null;
  masked_login_id: string | null;
  credential_version: number;
  last_auth_status: string;
  last_authenticated_at: string | null;
  updated_at: string | null;
}

const providers: ReadonlySet<string> = new Set(["KORAIL", "SRT"]);
const loginMethods: ReadonlySet<string> = new Set([
  "membership_number",
  "email",
  "phone",
]);
const authStatuses: ReadonlySet<string> = new Set([
  "not_checked",
  "authenticated",
  "auth_required",
  "provider_blocked",
  "failed",
]);

function csrfToken(): string {
  const entry = document.cookie
    .split("; ")
    .find((value) => value.startsWith("rail_csrf="));
  return entry ? decodeURIComponent(entry.slice("rail_csrf=".length)) : "";
}

async function request(path: string, init: RequestInit = {}): Promise<unknown> {
  const method = init.method ?? "GET";
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body !== undefined) headers.set("Content-Type", "application/json");
  if (!new Set(["GET", "HEAD", "OPTIONS"]).has(method.toUpperCase())) {
    const csrf = csrfToken();
    if (csrf) headers.set("X-CSRF-Token", csrf);
  }
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    method,
    headers,
    credentials: "include",
  });
  if (response.status === 204) return null;
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 404) {
      throw new Error(
        "철도 계정 API가 현재 서버에 반영되지 않았습니다. 서버를 최신 버전으로 재배포해 주세요.",
      );
    }
    const message = typeof payload === "object" && payload !== null && "detail" in payload
      && typeof payload.detail === "string"
      ? payload.detail
      : "철도 계정 설정을 처리하지 못했습니다.";
    throw new Error(message);
  }
  return payload;
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function mapProviderAccount(value: unknown): ProviderAccount {
  if (typeof value !== "object" || value === null) {
    throw new Error("철도 계정 응답 형식을 확인할 수 없습니다.");
  }
  const dto = value as Partial<ProviderAccountDto>;
  const provider = String(dto.provider ?? "").toUpperCase();
  if (
    !providers.has(provider)
    || typeof dto.configured !== "boolean"
    || typeof dto.enabled !== "boolean"
    || !(dto.login_method === null || loginMethods.has(String(dto.login_method ?? "")))
    || !isNullableString(dto.masked_login_id)
    || !Number.isInteger(dto.credential_version)
    || !authStatuses.has(String(dto.last_auth_status ?? ""))
    || !isNullableString(dto.last_authenticated_at)
    || !isNullableString(dto.updated_at)
  ) {
    throw new Error("철도 계정 응답 형식을 확인할 수 없습니다.");
  }
  return {
    provider: provider as RailProvider,
    configured: dto.configured,
    enabled: dto.enabled,
    loginMethod: dto.login_method as ProviderLoginMethod | null,
    maskedLoginId: dto.masked_login_id,
    credentialVersion: dto.credential_version as number,
    lastAuthStatus: dto.last_auth_status as ProviderAuthStatus,
    lastAuthenticatedAt: dto.last_authenticated_at,
    updatedAt: dto.updated_at,
  };
}

export async function fetchProviderAccounts(): Promise<ProviderAccount[]> {
  const payload = await request("/provider-accounts", { cache: "no-store" });
  if (!Array.isArray(payload)) throw new Error("철도 계정 목록을 확인할 수 없습니다.");
  return payload.map(mapProviderAccount);
}

export async function saveProviderAccount(
  provider: RailProvider,
  input: ProviderAccountCredentialInput,
): Promise<ProviderAccount> {
  return mapProviderAccount(await request(`/provider-accounts/${provider.toLowerCase()}`, {
    method: "PUT",
    body: JSON.stringify({
      login_method: input.loginMethod,
      login_id: input.loginId,
      password: input.password,
      enabled: input.enabled ?? true,
    }),
  }));
}

export async function deleteProviderAccount(provider: RailProvider): Promise<void> {
  await request(`/provider-accounts/${provider.toLowerCase()}`, { method: "DELETE" });
}
