import { useCallback, useEffect, useState } from "react";

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
  const [loaded, setLoaded] = useState(demo);
  const [loading, setLoading] = useState(false);
  const [pendingProvider, setPendingProvider] = useState<RailProvider | null>(null);

  const refreshRuntimeStatuses = useCallback(async (): Promise<void> => {
    if (demo) {
      setRuntimeStatuses(demoProviderRuntimeStatuses);
      return;
    }
    setRuntimeStatuses(await loadRuntimeStatuses());
  }, [demo, loadRuntimeStatuses]);

  const onProviderAuthenticationTransition = useCallback((): void => {
    if (demo) return;
    setLoaded(false);
    void loadAccounts().then((items) => {
      setAccounts(items);
      setLoaded(true);
      return refreshRuntimeStatuses();
    }).catch(() => {
      // Keep account-derived actions fail-closed until a no-store account read succeeds.
    });
  }, [demo, loadAccounts, refreshRuntimeStatuses]);

  useEffect(() => {
    if (!authenticated) return undefined;
    if (demo) return undefined;
    let active = true;
    void Promise.resolve().then(() => {
      if (!active) return;
      setLoading(true);
      setLoaded(false);
      void loadAccounts().then((items) => {
        if (active) {
          setAccounts(items);
          setLoaded(true);
          void refreshRuntimeStatuses().catch(() => {
            // Preserve the latest known runtime state when this supplemental read fails.
          });
        }
      }).catch((reason: unknown) => {
        if (active) pushToast(errorMessage(reason, "철도 계정 상태를 불러오지 못했습니다."));
      }).finally(() => {
        if (active) setLoading(false);
      });
    });
    return () => {
      active = false;
    };
  }, [authenticated, demo, loadAccounts, pushToast, refreshRuntimeStatuses]);

  useEffect(() => {
    if (!authenticated || demo || !runtimePollingEnabled) return undefined;
    const poll = (): void => {
      void loadRuntimeStatuses().then((statuses) => {
        setRuntimeStatuses(statuses);
      }).catch(() => {
        // A polling failure must not erase a previously confirmed status or interrupt settings.
      });
    };
    poll();
    const timer = window.setInterval(() => {
      poll();
    }, RUNTIME_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [authenticated, demo, loadRuntimeStatuses, runtimePollingEnabled]);

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
      void refreshRuntimeStatuses().catch(() => {
        // The saved account remains valid if its supplemental runtime read is unavailable.
      });
      pushToast(`${provider} 철도 계정을 저장했습니다.`);
    } catch (reason: unknown) {
      pushToast(errorMessage(reason, "철도 계정을 저장하지 못했습니다."));
      throw reason;
    } finally {
      setPendingProvider(null);
    }
  }, [demo, now, persistAccount, pushToast, refreshRuntimeStatuses]);

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
      void refreshRuntimeStatuses().catch(() => {
        // Preserve the prior status instead of replacing it with an unverified value.
      });
      pushToast(`${provider} 철도 계정 연결을 해제했습니다.`);
    } catch (reason: unknown) {
      pushToast(errorMessage(reason, "철도 계정 연결을 해제하지 못했습니다."));
      throw reason;
    } finally {
      setPendingProvider(null);
    }
  }, [demo, pushToast, refreshRuntimeStatuses, removeAccount]);

  const accountAuthStatusFor = useCallback((provider: string): ProviderAuthStatus | null => {
    if (!loaded || (provider !== "KORAIL" && provider !== "SRT")) return null;
    const account = accounts.find((item) => item.provider === provider);
    return account?.configured && account.enabled ? account.lastAuthStatus : "not_checked";
  }, [accounts, loaded]);

  const reset = useCallback((): void => {
    setAccounts([]);
    setRuntimeStatuses([]);
    setLoaded(false);
    setLoading(false);
    setPendingProvider(null);
  }, []);

  return {
    accounts,
    runtimeStatuses,
    loading,
    pendingProvider,
    saveAccount,
    deleteAccount,
    onProviderAuthenticationTransition,
    accountAuthStatusFor,
    reset,
  };
}
