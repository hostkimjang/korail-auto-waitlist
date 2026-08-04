import { useCallback, useEffect, useRef, useState } from "react";

import { DEMO_MODE } from "../../api.js";
import { getAuthStatus } from "../../api/auth";

export interface AuthState {
  loading: boolean;
  configured: boolean;
  authenticated: boolean;
  registrationAllowed: boolean;
  demo: boolean;
  error: string | null;
}

interface AuthStatusPayload {
  configured: boolean;
  authenticated: boolean;
  registrationAllowed: boolean;
}

const demoAuthState: AuthState = {
  loading: false,
  configured: true,
  authenticated: true,
  registrationAllowed: false,
  demo: true,
  error: null,
};

const initialAuthState: AuthState = DEMO_MODE
  ? demoAuthState
  : {
      loading: true,
      configured: false,
      authenticated: false,
      registrationAllowed: false,
      demo: false,
      error: null,
    };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function normalizeAuthStatus(payload: unknown): AuthStatusPayload {
  if (
    !isRecord(payload)
    || typeof payload.configured !== "boolean"
    || typeof payload.authenticated !== "boolean"
    || typeof payload.registration_allowed !== "boolean"
  ) {
    throw new Error("관리자 설정 상태 응답을 확인할 수 없습니다.");
  }

  return {
    configured: payload.configured,
    authenticated: payload.authenticated,
    registrationAllowed: payload.registration_allowed,
  };
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error && reason.message
    ? reason.message
    : "관리자 설정 상태를 확인하지 못했습니다.";
}

export function useAuthState() {
  const [auth, setAuth] = useState<AuthState>(initialAuthState);
  const requestSequence = useRef(0);

  const retryAuthStatus = useCallback(async (): Promise<void> => {
    if (DEMO_MODE) return;

    const requestId = ++requestSequence.current;
    setAuth((current) => ({ ...current, loading: true, error: null }));

    try {
      const payload: unknown = await getAuthStatus();
      const status = normalizeAuthStatus(payload);
      if (requestId !== requestSequence.current) return;
      setAuth({ ...status, loading: false, demo: false, error: null });
    } catch (reason) {
      if (requestId !== requestSequence.current) return;
      setAuth({
        loading: false,
        configured: false,
        authenticated: false,
        registrationAllowed: false,
        demo: false,
        error: errorMessage(reason),
      });
    }
  }, []);

  useEffect(() => {
    if (DEMO_MODE) return undefined;
    void retryAuthStatus();
    return () => {
      requestSequence.current += 1;
    };
  }, [retryAuthStatus]);

  const markAuthenticated = useCallback((): void => {
    setAuth((current) => ({
      ...current,
      loading: false,
      configured: true,
      authenticated: true,
      registrationAllowed: false,
      error: null,
    }));
  }, []);

  const markUnauthenticated = useCallback((): void => {
    setAuth((current) => ({ ...current, authenticated: false }));
  }, []);

  return { auth, markAuthenticated, markUnauthenticated, retryAuthStatus };
}
