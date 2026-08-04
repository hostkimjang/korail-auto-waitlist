import { useState } from "react";
import {
  LockKey,
  ShieldCheck,
  User,
  WarningCircle,
} from "@phosphor-icons/react";

import { ApiError, loginWithPassword, registerAdmin } from "../../api/auth";
import { Brand } from "../../shared/ui/Brand";
import type { AuthState } from "./useAuthState";

export interface AuthGateProps {
  status: AuthState;
  onAuthenticated: () => void;
  onRetryStatus: () => void | Promise<void>;
}

function messageFrom(reason: unknown): string {
  return reason instanceof Error && reason.message
    ? reason.message
    : "인증을 완료하지 못했습니다.";
}

interface CredentialFieldsProps {
  mode: "register" | "login";
  username: string;
  password: string;
  disabled: boolean;
  onUsernameChange: (value: string) => void;
  onPasswordChange: (value: string) => void;
}

function CredentialFields({
  mode,
  username,
  password,
  disabled,
  onUsernameChange,
  onPasswordChange,
}: CredentialFieldsProps) {
  return (
    <>
      <div className="field">
        <label htmlFor="admin-username"><User size={17} />관리자 ID</label>
        <input
          id="admin-username"
          name="username"
          value={username}
          onChange={(event) => onUsernameChange(event.target.value)}
          autoComplete="username"
          autoCapitalize="none"
          spellCheck={false}
          minLength={3}
          maxLength={64}
          pattern="[A-Za-z0-9][A-Za-z0-9._-]*"
          aria-describedby="admin-username-helper"
          disabled={disabled}
          required
        />
        <small id="admin-username-helper">영문자 또는 숫자로 시작하는 3~64자의 영문자·숫자·마침표·밑줄·하이픈을 사용할 수 있습니다.</small>
      </div>
      <div className="field">
        <label htmlFor="admin-password"><LockKey size={17} />비밀번호</label>
        <input
          id="admin-password"
          name="password"
          type="password"
          value={password}
          onChange={(event) => onPasswordChange(event.target.value)}
          autoComplete={mode === "register" ? "new-password" : "current-password"}
          minLength={mode === "register" ? 12 : undefined}
          maxLength={128}
          aria-describedby={mode === "register" ? "admin-password-helper" : undefined}
          disabled={disabled}
          required
        />
        {mode === "register" && <small id="admin-password-helper">12~128자로 설정하세요. 다른 서비스에서 사용하지 않는 비밀번호를 권장합니다.</small>}
      </div>
    </>
  );
}

export function AuthGate({ status, onAuthenticated, onRetryStatus }: AuthGateProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirmation, setPasswordConfirmation] = useState("");

  const perform = async (
    action: () => Promise<unknown>,
    retryStatusOnConflict = false,
  ): Promise<void> => {
    setBusy(true);
    setError("");
    try {
      await action();
      setPassword("");
      setPasswordConfirmation("");
      onAuthenticated();
    } catch (reason) {
      if (retryStatusOnConflict && reason instanceof ApiError && reason.status === 409) {
        setPassword("");
        setPasswordConfirmation("");
        await onRetryStatus();
        return;
      }
      setError(messageFrom(reason));
    } finally {
      setBusy(false);
    }
  };

  const submitRegistration = (): void => {
    const normalizedUsername = username.trim();
    if (!normalizedUsername || !password) return;
    if (password !== passwordConfirmation) {
      setError("비밀번호와 비밀번호 확인이 일치하지 않습니다.");
      return;
    }
    void perform(() => registerAdmin(normalizedUsername, password), true);
  };

  const submitLogin = (): void => {
    const normalizedUsername = username.trim();
    if (!normalizedUsername || !password) return;
    void perform(() => loginWithPassword(normalizedUsername, password));
  };

  if (status.error) {
    return (
      <main className="auth-page">
        <section className="auth-card auth-status-error" aria-labelledby="auth-status-error-title">
          <Brand />
          <div className="auth-intro">
            <WarningCircle size={42} weight="fill" />
            <h1 id="auth-status-error-title">서버 연결을 확인해 주세요</h1>
            <p>관리자 설정 상태를 확인하지 못해 계정 생성과 로그인을 안전하게 중단했습니다.</p>
          </div>
          <div className="form-error" role="alert"><WarningCircle weight="fill" />{status.error}</div>
          <button type="button" className="button button-primary auth-primary" onClick={() => void onRetryStatus()}>
            다시 확인
          </button>
        </section>
      </main>
    );
  }

  if (status.configured) {
    return (
      <main className="auth-page">
        <section className="auth-card" aria-labelledby="admin-login-title">
          <Brand />
          <div className="auth-intro">
            <ShieldCheck size={42} weight="fill" />
            <h1 id="admin-login-title">관리자 로그인</h1>
            <p>등록된 관리자 ID와 비밀번호로 로그인하세요.</p>
          </div>
          <form className="auth-form" onSubmit={(event) => { event.preventDefault(); submitLogin(); }}>
            <CredentialFields
              mode="login"
              username={username}
              password={password}
              disabled={busy}
              onUsernameChange={setUsername}
              onPasswordChange={setPassword}
            />
            <button type="submit" className="button button-primary auth-primary" disabled={busy || !username.trim() || !password}>
              {busy ? "로그인 중…" : "로그인"}
            </button>
          </form>
          {error && <div className="form-error" role="alert"><WarningCircle weight="fill" />{error}</div>}
          <div className="auth-note"><LockKey size={18} /><span>비밀번호와 카드·결제 인증정보는 화면이나 로그에 표시하지 않습니다.</span></div>
        </section>
      </main>
    );
  }

  return (
    <main className="auth-page">
      <section className="auth-card setup-card" aria-labelledby="admin-setup-title">
        <Brand />
        <div className="auth-intro">
          <ShieldCheck size={42} weight="fill" />
          <span className="auth-eyebrow">처음 한 번만 진행합니다</span>
          <h1 id="admin-setup-title">초기 관리자 등록</h1>
          <p>관리자 계정을 만들면 인증 정보가 PostgreSQL에 저장되고, 이후 접속부터는 로그인 화면만 표시됩니다.</p>
        </div>
        <form className="auth-form" onSubmit={(event) => { event.preventDefault(); submitRegistration(); }}>
          <CredentialFields
            mode="register"
            username={username}
            password={password}
            disabled={busy}
            onUsernameChange={setUsername}
            onPasswordChange={setPassword}
          />
          <div className="field">
            <label htmlFor="admin-password-confirmation"><LockKey size={17} />비밀번호 확인</label>
            <input
              id="admin-password-confirmation"
              name="password-confirmation"
              type="password"
              value={passwordConfirmation}
              onChange={(event) => setPasswordConfirmation(event.target.value)}
              autoComplete="new-password"
              minLength={12}
              maxLength={128}
              disabled={busy}
              required
            />
          </div>
          {!status.registrationAllowed && <div className="form-error" role="alert"><WarningCircle weight="fill" />현재 서버에서 최초 관리자 등록을 허용하지 않습니다.</div>}
          <button type="submit" className="button button-primary auth-primary" disabled={busy || !status.registrationAllowed || !username.trim() || !password || !passwordConfirmation}>
            {busy ? "계정 만드는 중…" : "관리자 계정 만들기"}
          </button>
        </form>
        {error && <div className="form-error" role="alert"><WarningCircle weight="fill" />{error}</div>}
        <div className="auth-note"><LockKey size={18} /><span>비밀번호 원문은 저장하지 않고 단방향 해시로 보호합니다.</span></div>
      </section>
    </main>
  );
}
