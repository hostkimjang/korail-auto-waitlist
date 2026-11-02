import { useCallback, useEffect, useRef, useState } from "react";

import {
  deleteProviderAccount,
  fetchProviderAccounts,
  saveProviderAccount,
  type ProviderAccount,
  type ProviderAccountCredentialInput,
  type ProviderAuthStatus,
  type RailProvider,
} from "../../api/providerAccounts";
import {
  fetchProviderRuntimeStatuses,
  type ProviderRuntimeStatus,
} from "../../api/providerRuntime";
import {
  demoProviderAccounts,
  demoProviderRuntimeStatuses,
} from "../../fixtures/demoData";
import type { ProviderRuntimeStatusLoadState } from "./providerRuntimeStatus";

export type ProviderAccountsLoader = () => Promise<ReadonlyArray<ProviderAccount>>;
export type ProviderRuntimeStatusesLoader = () => Promise<
  ReadonlyArray<ProviderRuntimeStatus>
>;
export type ProviderAccountPersister = (
  provider: RailProvider,
  input: ProviderAccountCredentialInput,
) => Promise<ProviderAccount>;
export type ProviderAccountRemover = (provider: RailProvider) => Promise<void>;

export interface UseProviderAccountSettingsOptions {
  authenticated: boolean;
  demo: boolean;
  runtimePollingEnabled: boolean;
  pushToast: (message: string) => void;
  loadAccounts?: ProviderAccountsLoader;
  loadRuntimeStatuses?: ProviderRuntimeStatusesLoader;
  persistAccount?: ProviderAccountPersister;
  removeAccount?: ProviderAccountRemover;
  now?: () => string;
}

export interface ProviderAccountSettingsController {
  accounts: ReadonlyArray<ProviderAccount>;
  runtimeStatuses: ReadonlyArray<ProviderRuntimeStatus>;
  runtimeStatusLoadState: ProviderRuntimeStatusLoadState;
  loading: boolean;
  pendingProvider: RailProvider | null;
  saveAccount: (
    provider: RailProvider,
    input: ProviderAccountCredentialInput,
  ) => Promise<void>;
  deleteAccount: (provider: RailProvider) => Promise<void>;
  onProviderAuthenticationTransition: () => void;
  accountAuthStatusFor: (provider: string) => ProviderAuthStatus | null;
  reset: () => void;
}

const RUNTIME_POLL_INTERVAL_MS = 15_000;

function currentIsoTimestamp(): string {
  return new Date().toISOString();
}

function errorMessage(reason: unknown, fallback: string): string {
  return reason instanceof Error && reason.message ? reason.message : fallback;
}

function demoAccountFor(provider: RailProvider): ProviderAccount {
  const account = demoProviderAccounts.find((item) => item.provider === provider);
  if (account === undefined) {
    throw new Error(`${provider} 데모 철도 계정 정보를 찾지 못했습니다.`);
  }
  return account;
}

export function useProviderAccountSettings({
  authenticated,
  demo,
  runtimePollingEnabled,
  pushToast,
  loadAccounts = fetchProviderAccounts,
  loadRuntimeStatuses = fetchProviderRuntimeStatuses,
  persistAccount = saveProviderAccount,
  removeAccount = deleteProviderAccount,
  now = currentIsoTimestamp,
}: UseProviderAccountSettingsOptions): ProviderAccountSettingsController {
  const [accounts, setAccounts] = useState<ReadonlyArray<ProviderAccount>>(
    () => demo ? demoProviderAccounts : [],
  );
  const [runtimeStatuses, setRuntimeStatuses] = useState<
    ReadonlyArray<ProviderRuntimeStatus>
  >(() => demo ? demoProviderRuntimeStatuses : []);
  const [runtimeStatusLoadState, setRuntimeStatusLoadState] =
    useState<ProviderRuntimeStatusLoadState>(demo ? "ready" : "idle");
  const [loaded, setLoaded] = useState(demo);
  const [loading, setLoading] = useState(false);
  const [pendingProvider, setPendingProvider] = useState<RailProvider | null>(null);
  const mountedRef = useRef(true);
  const lifecycleRevisionRef = useRef(0);
  const accountRequestRevisionRef = useRef(0);
  const runtimeRequestRevisionRef = useRef(0);
  const runtimePollingEnabledRef = useRef(runtimePollingEnabled);

  useEffect(() => {
    runtimePollingEnabledRef.current = runtimePollingEnabled;
  }, [runtimePollingEnabled]);

  useEffect(() => {
    // React StrictMode replays effect setup/cleanup in development. Restore the
    // mounted marker on every setup while invalidating requests on each cleanup.
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      lifecycleRevisionRef.current += 1;
      accountRequestRevisionRef.current += 1;
      runtimeRequestRevisionRef.current += 1;
    };
  }, []);

  const invalidateRuntimeStatuses = useCallback((): void => {
    runtimeRequestRevisionRef.current += 1;
    if (!mountedRef.current) return;
    setRuntimeStatuses([]);
    setRuntimeStatusLoadState("idle");
  }, []);

  const refreshRuntimeStatuses = useCallback(async (
    callerIsActive: () => boolean = () => true,
  ): Promise<void> => {
    if (demo) {
      if (mountedRef.current && callerIsActive()) {
        setRuntimeStatuses(demoProviderRuntimeStatuses);
        setRuntimeStatusLoadState("ready");
      }
      return;
    }
    const requestRevision = runtimeRequestRevisionRef.current + 1;
    runtimeRequestRevisionRef.current = requestRevision;
    const lifecycleRevision = lifecycleRevisionRef.current;
    const canCommit = (): boolean => mountedRef.current
      && callerIsActive()
      && lifecycleRevisionRef.current === lifecycleRevision
      && runtimeRequestRevisionRef.current === requestRevision;
    if (canCommit()) {
      setRuntimeStatusLoadState((current) => current === "ready" ? current : "loading");
    }
    try {
      const statuses = await loadRuntimeStatuses();
      if (!canCommit()) return;
      setRuntimeStatuses(statuses);
      setRuntimeStatusLoadState("ready");
    } catch (reason: unknown) {
      if (canCommit()) {
        setRuntimeStatuses([]);
        setRuntimeStatusLoadState("unavailable");
      }
      throw reason;
    }
  }, [demo, loadRuntimeStatuses]);

  const onProviderAuthenticationTransition = useCallback((): void => {
    if (demo) return;
    const requestRevision = accountRequestRevisionRef.current + 1;
    accountRequestRevisionRef.current = requestRevision;
    const lifecycleRevision = lifecycleRevisionRef.current;
    const canCommit = (): boolean => mountedRef.current
      && lifecycleRevisionRef.current === lifecycleRevision
      && accountRequestRevisionRef.current === requestRevision;
    invalidateRuntimeStatuses();
    setLoaded(false);
    setLoading(false);
    void loadAccounts().then((items) => {
      if (!canCommit()) return;
      setAccounts(items);
      setLoaded(true);
      if (!runtimePollingEnabledRef.current) {
        return refreshRuntimeStatuses(canCommit);
      }
      return undefined;
    }).catch(() => {
      // Keep account-derived actions fail-closed until a no-store account read succeeds.
    });
  }, [demo, invalidateRuntimeStatuses, loadAccounts, refreshRuntimeStatuses]);

  useEffect(() => {
    if (!authenticated) return undefined;
    if (demo) return undefined;
    let active = true;
    const requestRevision = accountRequestRevisionRef.current + 1;
    accountRequestRevisionRef.current = requestRevision;
    const lifecycleRevision = lifecycleRevisionRef.current;
    const canCommit = (): boolean => active
      && mountedRef.current
      && lifecycleRevisionRef.current === lifecycleRevision
      && accountRequestRevisionRef.current === requestRevision;
    void Promise.resolve().then(() => {
      if (!canCommit()) return;
      setLoading(true);
      setLoaded(false);
      void loadAccounts().then((items) => {
        if (canCommit()) {
          setAccounts(items);
          setLoaded(true);
          if (!runtimePollingEnabledRef.current) {
            void refreshRuntimeStatuses(canCommit).catch(() => {
              // The runtime state is cleared and marked unavailable by the shared boundary.
            });
          }
        }
      }).catch((reason: unknown) => {
        if (canCommit()) pushToast(errorMessage(reason, "철도 계정 상태를 불러오지 못했습니다."));
      }).finally(() => {
        if (canCommit()) setLoading(false);
      });
    });
    return () => {
      active = false;
      accountRequestRevisionRef.current += 1;
    };
  }, [authenticated, demo, loadAccounts, pushToast, refreshRuntimeStatuses]);

  useEffect(() => {
    if (!authenticated || demo || !loaded || !runtimePollingEnabled) return undefined;
    let active = true;
    let pollingGeneration = 0;
    const poll = (): void => {
      const generation = pollingGeneration;
      void refreshRuntimeStatuses(() => active && pollingGeneration === generation).catch(() => {
        // A polling failure is represented by the fail-closed unavailable state.
      });
    };
    let timer: number | null = null;
    const stopPolling = (): void => {
      pollingGeneration += 1;
      if (timer !== null) window.clearInterval(timer);
      timer = null;
    };
    const startPolling = (): void => {
      stopPolling();
      if (document.visibilityState === "hidden") return;
      poll();
      timer = window.setInterval(poll, RUNTIME_POLL_INTERVAL_MS);
    };
    const handleVisibilityChange = (): void => {
      if (document.visibilityState === "hidden") stopPolling();
      else startPolling();
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    startPolling();
    return () => {
      active = false;
      stopPolling();
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [authenticated, demo, loaded, refreshRuntimeStatuses, runtimePollingEnabled]);

  const saveAccount = useCallback(async (
    provider: RailProvider,
    input: ProviderAccountCredentialInput,
  ): Promise<void> => {
    setPendingProvider(provider);
    try {
      const saved = demo
        ? {
          ...demoAccountFor(provider),
          maskedLoginId: `${input.loginId.slice(0, 2)}***`,
          updatedAt: now(),
        }
        : await persistAccount(provider, input);
      setAccounts((items) => [
        saved,
        ...items.filter((item) => item.provider !== provider),
      ]);
      setLoaded(true);
      invalidateRuntimeStatuses();
      void refreshRuntimeStatuses().catch(() => {
        // The saved account remains valid while runtime reuse stays fail-closed.
      });
      pushToast(`${provider} 철도 계정을 저장했습니다.`);
    } catch (reason: unknown) {
      pushToast(errorMessage(reason, "철도 계정을 저장하지 못했습니다."));
      throw reason;
    } finally {
      setPendingProvider(null);
    }
  }, [demo, invalidateRuntimeStatuses, now, persistAccount, pushToast, refreshRuntimeStatuses]);

  const deleteAccount = useCallback(async (provider: RailProvider): Promise<void> => {
    setPendingProvider(provider);
    try {
      if (!demo) await removeAccount(provider);
      setAccounts((items) => items.map((item) => item.provider === provider ? {
        ...item,
        configured: false,
        enabled: false,
        maskedLoginId: null,
        credentialVersion: 0,
        lastAuthStatus: "not_checked",
        lastAuthenticatedAt: null,
        updatedAt: null,
      } : item));
      setLoaded(true);
      invalidateRuntimeStatuses();
      void refreshRuntimeStatuses().catch(() => {
        // The disconnected account remains valid while runtime reuse stays fail-closed.
      });
      pushToast(`${provider} 철도 계정 연결을 해제했습니다.`);
    } catch (reason: unknown) {
      pushToast(errorMessage(reason, "철도 계정 연결을 해제하지 못했습니다."));
      throw reason;
    } finally {
      setPendingProvider(null);
    }
  }, [demo, invalidateRuntimeStatuses, pushToast, refreshRuntimeStatuses, removeAccount]);

  const accountAuthStatusFor = useCallback((provider: string): ProviderAuthStatus | null => {
    if (!loaded || (provider !== "KORAIL" && provider !== "SRT")) return null;
    const account = accounts.find((item) => item.provider === provider);
    return account?.configured && account.enabled ? account.lastAuthStatus : "not_checked";
  }, [accounts, loaded]);

  const reset = useCallback((): void => {
    lifecycleRevisionRef.current += 1;
    accountRequestRevisionRef.current += 1;
    runtimeRequestRevisionRef.current += 1;
    setAccounts([]);
    setRuntimeStatuses([]);
    setRuntimeStatusLoadState("idle");
    setLoaded(false);
    setLoading(false);
    setPendingProvider(null);
  }, []);

  return {
    accounts,
    runtimeStatuses,
    runtimeStatusLoadState,
    loading,
    pendingProvider,
    saveAccount,
    deleteAccount,
    onProviderAuthenticationTransition,
    accountAuthStatusFor,
    reset,
  };
}
