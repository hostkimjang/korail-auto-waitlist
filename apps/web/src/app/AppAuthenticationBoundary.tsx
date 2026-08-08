import type { ReactElement } from "react";

import { AuthGate } from "../features/auth/AuthGate";
import type { AuthState } from "../features/auth/useAuthState";

export interface AppAuthenticationBoundaryProps {
  status: AuthState;
  onAuthenticated: () => void;
  onRetryStatus: () => void | Promise<void>;
  children: ReactElement;
}

export function AppAuthenticationBoundary({
  status,
  onAuthenticated,
  onRetryStatus,
  children,
}: AppAuthenticationBoundaryProps): ReactElement {
  if (status.loading) {
    return (
      <main className="auth-page">
        <div className="loading-state">
          <img src="/icons/app-icon-any-512-v2.png" alt="" />
          <span>안전하게 연결하는 중…</span>
        </div>
      </main>
    );
  }

  if (!status.authenticated) {
    return (
      <AuthGate
        status={status}
        onAuthenticated={onAuthenticated}
        onRetryStatus={onRetryStatus}
      />
    );
  }

  return children;
}
