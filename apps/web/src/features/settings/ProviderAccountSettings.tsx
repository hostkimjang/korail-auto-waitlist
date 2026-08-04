import { CheckCircle, Key, SignOut, WarningCircle } from "@phosphor-icons/react";
import { useState } from "react";

import type { ProviderRuntimeStatus } from "../../api/providerRuntime";
import type {
  ProviderAccount,
  ProviderAccountCredentialInput,
  ProviderLoginMethod,
  RailProvider,
} from "../../api/providerAccounts";
import {
  formatRuntimeLocalReuseWindow,
  formatRuntimeVerifiedAge,
  providerRuntimeStatusPresentation,
} from "./providerRuntimeStatus";
import "./providerRuntimeStatus.css";

export interface ProviderAccountSettingsProps {
  accounts: ReadonlyArray<ProviderAccount>;
  runtimeStatuses?: ReadonlyArray<ProviderRuntimeStatus>;
  loading?: boolean;
  pendingProvider?: RailProvider | null;
  onSave: (provider: RailProvider, input: ProviderAccountCredentialInput) => Promise<void>;
  onDelete: (provider: RailProvider) => Promise<void>;
}

const providerLabels: Record<RailProvider, string> = {
  KORAIL: "KORAIL",
  SRT: "SRT",
};

const statusLabels: Record<ProviderAccount["lastAuthStatus"], string> = {
  not_checked: "로그인 확인 전",
  authenticated: "로그인 확인됨",
  auth_required: "로그인 정보 확인 필요",
  provider_blocked: "운영사 확인 필요",
  failed: "로그인 확인 실패",
};

const loginMethodLabels: Record<ProviderLoginMethod, string> = {
  membership_number: "회원번호",
  email: "이메일",
  phone: "휴대전화",
};

const loginMethods = Object.keys(loginMethodLabels) as ProviderLoginMethod[];

interface CredentialDraft {
  loginMethod: ProviderLoginMethod;
  loginId: string;
  password: string;
}

const emptyDraft = (loginMethod: ProviderLoginMethod = "membership_number"): CredentialDraft => ({
  loginMethod,
  loginId: "",
  password: "",
});

function loginFieldContract(loginMethod: ProviderLoginMethod): {
  inputMode: "email" | "numeric" | "tel";
  label: string;
  placeholder: string;
  type: "email" | "tel" | "text";
} {
  if (loginMethod === "email") {
    return {
      inputMode: "email",
      label: "이메일 주소",
      placeholder: "name@example.com",
      type: "email",
    };
  }
  if (loginMethod === "phone") {
    return {
      inputMode: "tel",
      label: "휴대전화 번호",
      placeholder: "010-1234-5678",
      type: "tel",
    };
  }
  return {
    inputMode: "numeric",
    label: "회원번호",
    placeholder: "회원번호를 입력하세요",
    type: "text",
  };
}

function validateDraft(draft: CredentialDraft): string | null {
  const loginId = draft.loginId.trim();
  if (!loginId || !draft.password) {
    return `${loginMethodLabels[draft.loginMethod]}와 비밀번호를 모두 입력해 주세요.`;
  }
  if (draft.loginMethod === "email" && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(loginId)) {
    return "올바른 이메일 주소를 입력해 주세요.";
  }
  if (draft.loginMethod === "phone") {
    const digits = loginId.replace(/\D/g, "");
    if (digits.length < 10 || digits.length > 11) {
      return "휴대전화 번호를 확인해 주세요.";
    }
  }
  return null;
}

function emptyAccount(provider: RailProvider): ProviderAccount {
  return {
    provider,
    configured: false,
    enabled: false,
    loginMethod: null,
    maskedLoginId: null,
    credentialVersion: 0,
    lastAuthStatus: "not_checked",
    lastAuthenticatedAt: null,
    updatedAt: null,
  };
}

export function ProviderAccountSettings({
  accounts,
  runtimeStatuses = [],
  loading = false,
  pendingProvider = null,
  onSave,
  onDelete,
}: ProviderAccountSettingsProps) {
  const [editing, setEditing] = useState<RailProvider | null>(null);
  const [drafts, setDrafts] = useState<Record<RailProvider, CredentialDraft>>({
    KORAIL: emptyDraft(),
    SRT: emptyDraft(),
  });
  const [errors, setErrors] = useState<Partial<Record<RailProvider, string>>>({});

  const updateDraft = (provider: RailProvider, update: Partial<CredentialDraft>): void => {
    setDrafts((current) => ({
      ...current,
      [provider]: { ...current[provider], ...update },
    }));
  };

  const beginEdit = (provider: RailProvider): void => {
    const account = accounts.find((item) => item.provider === provider);
    setEditing(provider);
    setDrafts((current) => ({
      ...current,
      [provider]: emptyDraft(account?.loginMethod ?? "membership_number"),
    }));
    setErrors((current) => ({ ...current, [provider]: undefined }));
  };

  const save = async (provider: RailProvider): Promise<void> => {
    const draft = drafts[provider];
    const validationError = validateDraft(draft);
    if (validationError) {
      setErrors((current) => ({ ...current, [provider]: validationError }));
      return;
    }
    setErrors((current) => ({ ...current, [provider]: undefined }));
    try {
      await onSave(provider, {
        loginMethod: draft.loginMethod,
        loginId: draft.loginId.trim(),
        password: draft.password,
        enabled: true,
      });
      setDrafts((current) => ({ ...current, [provider]: emptyDraft(draft.loginMethod) }));
      setEditing(null);
    } catch (reason) {
      updateDraft(provider, { password: "" });
      setErrors((current) => ({
        ...current,
        [provider]: reason instanceof Error
          ? reason.message
          : "로그인 정보를 확인하지 못해 저장하지 않았습니다.",
      }));
    }
  };

  const remove = async (provider: RailProvider): Promise<void> => {
    setErrors((current) => ({ ...current, [provider]: undefined }));
    try {
      await onDelete(provider);
      if (editing === provider) setEditing(null);
    } catch (reason) {
      setErrors((current) => ({
        ...current,
        [provider]: reason instanceof Error
          ? reason.message
          : "철도 계정 연결을 해제하지 못했습니다.",
      }));
    }
  };

  return (
    <div className="provider-account-settings" aria-busy={loading || undefined}>
      {loading ? <p role="status">철도 계정 상태를 불러오는 중…</p> : null}
      {(["KORAIL", "SRT"] as const).map((provider) => {
        const account = accounts.find((item) => item.provider === provider) ?? emptyAccount(provider);
        const runtimeStatus = runtimeStatuses.find((item) => item.provider === provider) ?? null;
        const runtimePresentation = runtimeStatus
          ? providerRuntimeStatusPresentation(runtimeStatus)
          : null;
        const isPending = pendingProvider === provider;
        const isEditing = editing === provider || !account.configured;
        const draft = drafts[provider];
        const loginField = loginFieldContract(draft.loginMethod);
        const statusTone = account.lastAuthStatus === "authenticated" ? "is-ready" : account.configured ? "is-warning" : "is-empty";
        return (
          <section className="provider-account-card" key={provider} aria-labelledby={`provider-account-${provider}`}>
            <header>
              <span className={`provider-chip ${provider === "SRT" ? "provider-srt" : "provider-korail"}`}>{providerLabels[provider]}</span>
              <div>
                <h3 id={`provider-account-${provider}`}>{providerLabels[provider]} 계정</h3>
                <span className={`provider-account-status ${statusTone}`}>
                  {account.lastAuthStatus === "authenticated" ? <CheckCircle weight="fill" aria-hidden="true" /> : <WarningCircle weight="fill" aria-hidden="true" />}
                  {account.configured ? statusLabels[account.lastAuthStatus] : "연결 안 됨"}
                </span>
              </div>
            </header>
            <div
              className={`provider-runtime-status${runtimePresentation ? ` is-${runtimePresentation.tone}` : ""}`}
              aria-live="polite"
            >
              <div className="provider-runtime-status-heading">
                <span>상주 세션</span>
                <strong>{runtimePresentation?.label ?? "상태 확인 중"}</strong>
              </div>
              {runtimeStatus ? (
                <p className="provider-runtime-status-details">
                  <span>최근 재검증: {formatRuntimeVerifiedAge(runtimeStatus.lastVerifiedAgeSeconds)}</span>
                  <span>서버 로컬 재사용 창: {formatRuntimeLocalReuseWindow(
                    runtimeStatus.localReuseRemainingSeconds,
                    runtimeStatus.locallyReusable,
                  )}</span>
                </p>
              ) : (
                <p className="provider-runtime-status-details">상주 세션 상태를 확인하는 중입니다.</p>
              )}
            </div>
            {account.configured && !isEditing ? (
              <div className="provider-account-summary">
                <div className="provider-account-summary-grid">
                  <div><span>로그인 방식</span><strong>{loginMethodLabels[account.loginMethod ?? "membership_number"]}</strong></div>
                  <div><span>저장된 계정</span><strong>{account.maskedLoginId ?? "마스킹 정보 없음"}</strong></div>
                </div>
                <p>비밀번호는 암호화 저장되며 화면에 다시 표시하지 않습니다.</p>
                <div className="provider-account-actions">
                  <button className="button button-outline" type="button" disabled={isPending} onClick={() => beginEdit(provider)}><Key aria-hidden="true" />로그인 정보 변경</button>
                  <button className="button button-danger-outline" type="button" disabled={isPending} onClick={() => remove(provider)}><SignOut aria-hidden="true" />연결 해제</button>
                </div>
              </div>
            ) : (
              <div
                className="provider-account-editor"
                data-form-type="other"
                data-lpignore="true"
                data-1p-ignore="true"
              >
                <fieldset className="provider-login-methods">
                  <legend>로그인 방식</legend>
                  <div>
                    {loginMethods.map((loginMethod) => (
                      <label key={loginMethod} className={draft.loginMethod === loginMethod ? "is-selected" : ""}>
                        <input
                          type="radio"
                          name={`provider-login-method-${provider}`}
                          value={loginMethod}
                          checked={draft.loginMethod === loginMethod}
                          disabled={isPending}
                          onChange={() => updateDraft(provider, { loginMethod, loginId: "" })}
                        />
                        <span>{loginMethodLabels[loginMethod]}</span>
                      </label>
                    ))}
                  </div>
                </fieldset>
                <label>
                  <span>{loginField.label}</span>
                  <input
                    type={loginField.type}
                    inputMode={loginField.inputMode}
                    name={`provider-login-id-${provider.toLowerCase()}`}
                    value={draft.loginId}
                    placeholder={loginField.placeholder}
                    onChange={(event) => updateDraft(provider, { loginId: event.target.value })}
                    autoComplete="off"
                    data-lpignore="true"
                    data-1p-ignore="true"
                    disabled={isPending}
                  />
                </label>
                <label>
                  <span>비밀번호</span>
                  <input
                    type="password"
                    name={`provider-password-${provider.toLowerCase()}`}
                    value={draft.password}
                    onChange={(event) => updateDraft(provider, { password: event.target.value })}
                    autoComplete="new-password"
                    data-lpignore="true"
                    data-1p-ignore="true"
                    disabled={isPending}
                  />
                </label>
                <p>로그인 성공을 확인한 경우에만 암호화해 저장합니다. 카카오·애플·비회원 로그인은 지원하지 않습니다.</p>
                {errors[provider] ? <p className="provider-account-error" role="alert">{errors[provider]}</p> : null}
                <div className="provider-account-actions">
                  {account.configured ? <button className="button button-ghost" type="button" disabled={isPending} onClick={() => setEditing(null)}>취소</button> : null}
                  <button className="button button-primary" type="button" disabled={isPending} onClick={() => save(provider)}>{isPending ? "로그인 확인 중…" : "로그인 확인 후 저장"}</button>
                </div>
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
