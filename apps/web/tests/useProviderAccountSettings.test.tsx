import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  ProviderAccount,
  ProviderAccountCredentialInput,
  RailProvider,
} from "../src/api/providerAccounts";
import type { ProviderRuntimeStatus } from "../src/api/providerRuntime";
import {
  useProviderAccountSettings,
  type ProviderAccountPersister,
  type ProviderAccountRemover,
  type ProviderAccountsLoader,
  type ProviderRuntimeStatusesLoader,
} from "../src/features/settings/useProviderAccountSettings";

interface HookProps {
  authenticated: boolean;
  demo: boolean;
  runtimePollingEnabled: boolean;
}

const defaultHookProps: HookProps = {
  authenticated: true,
  demo: false,
  runtimePollingEnabled: false,
};

const credentialInput: ProviderAccountCredentialInput = {
  loginMethod: "membership_number",
  loginId: "1234567890",
  password: "placeholder-password",
  enabled: true,
};

function account(
  provider: RailProvider,
  overrides: Partial<ProviderAccount> = {},
): ProviderAccount {
  return {
    provider,
    configured: true,
    enabled: true,
    loginMethod: "membership_number",
    maskedLoginId: provider === "KORAIL" ? "12***" : "34***",
    credentialVersion: 1,
    lastAuthStatus: "authenticated",
    lastAuthenticatedAt: "2026-08-05T00:00:00Z",
    updatedAt: "2026-08-05T00:00:00Z",
    ...overrides,
  };
}

function runtimeStatus(
  provider: RailProvider,
  overrides: Partial<ProviderRuntimeStatus> = {},
): ProviderRuntimeStatus {
  return {
    provider,
    state: "ready",
    credentialGeneration: "generation-one",
    createdAgeSeconds: 10,
    lastVerifiedAgeSeconds: 5,
    lastUsedAgeSeconds: 3,
    localReuseRemainingSeconds: 100,
    locallyReusable: true,
    prewarmOutcome: "authenticated",
    ...overrides,
  };
}

function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
} {
  let resolvePromise: ((value: T) => void) | undefined;
  let rejectPromise: ((reason: unknown) => void) | undefined;
  const promise = new Promise<T>((resolve, reject) => {
    resolvePromise = resolve;
    rejectPromise = reject;
  });
  return {
    promise,
    resolve: (value) => resolvePromise?.(value),
    reject: (reason) => rejectPromise?.(reason),
  };
}

function testDependencies() {
  const loadAccounts = vi.fn<ProviderAccountsLoader>().mockResolvedValue([]);
  const loadRuntimeStatuses = vi.fn<ProviderRuntimeStatusesLoader>().mockResolvedValue([]);
  const persistAccount = vi.fn<ProviderAccountPersister>();
  const removeAccount = vi.fn<ProviderAccountRemover>().mockResolvedValue(undefined);
  const now = vi.fn<() => string>().mockReturnValue("2026-08-05T12:00:00Z");
  return { loadAccounts, loadRuntimeStatuses, persistAccount, removeAccount, now };
}

function renderController(
  dependencies: ReturnType<typeof testDependencies>,
  initialProps: HookProps = defaultHookProps,
) {
  const pushToast = vi.fn<(message: string) => void>();
  const hook = renderHook((props: HookProps) => useProviderAccountSettings({
    ...props,
    pushToast,
    ...dependencies,
  }), { initialProps });
  return { ...hook, pushToast };
}

describe("useProviderAccountSettings", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("does not perform provider I/O before authentication", () => {
    const dependencies = testDependencies();
    const { result } = renderController(dependencies, {
      authenticated: false,
      demo: false,
      runtimePollingEnabled: true,
    });

    expect(dependencies.loadAccounts).not.toHaveBeenCalled();
    expect(dependencies.loadRuntimeStatuses).not.toHaveBeenCalled();
    expect(result.current.accounts).toEqual([]);
    expect(result.current.runtimeStatuses).toEqual([]);
    expect(result.current.loading).toBe(false);
  });

  it("loads live accounts before their best-effort runtime status", async () => {
    const dependencies = testDependencies();
    const pendingAccounts = deferred<ReadonlyArray<ProviderAccount>>();
    const loadedAccounts = [account("KORAIL")];
    const loadedRuntime = [runtimeStatus("KORAIL")];
    dependencies.loadAccounts.mockReturnValue(pendingAccounts.promise);
    dependencies.loadRuntimeStatuses.mockResolvedValue(loadedRuntime);
    const { result } = renderController(dependencies);

    await waitFor(() => expect(dependencies.loadAccounts).toHaveBeenCalledOnce());
    expect(result.current.loading).toBe(true);
    expect(dependencies.loadRuntimeStatuses).not.toHaveBeenCalled();
    expect(result.current.accountAuthStatusFor("KORAIL")).toBeNull();

    await act(async () => {
      pendingAccounts.resolve(loadedAccounts);
      await pendingAccounts.promise;
    });

    await waitFor(() => expect(result.current.runtimeStatuses).toEqual(loadedRuntime));
    expect(result.current.accounts).toEqual(loadedAccounts);
    expect(result.current.loading).toBe(false);
    expect(result.current.accountAuthStatusFor("KORAIL")).toBe("authenticated");
  });

  it("reports an initial account failure safely and skips runtime loading", async () => {
    const dependencies = testDependencies();
    dependencies.loadAccounts.mockRejectedValue("malformed failure");
    const { result, pushToast } = renderController(dependencies);

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(pushToast).toHaveBeenCalledWith("철도 계정 상태를 불러오지 못했습니다.");
    expect(dependencies.loadRuntimeStatuses).not.toHaveBeenCalled();
    expect(result.current.accountAuthStatusFor("KORAIL")).toBeNull();
  });

  it("ignores an initial account result after unmount", async () => {
    const dependencies = testDependencies();
    const pendingAccounts = deferred<ReadonlyArray<ProviderAccount>>();
    dependencies.loadAccounts.mockReturnValue(pendingAccounts.promise);
    const { result, unmount } = renderController(dependencies);
    await waitFor(() => expect(dependencies.loadAccounts).toHaveBeenCalledOnce());

    unmount();
    await act(async () => {
      pendingAccounts.resolve([account("KORAIL")]);
      await pendingAccounts.promise;
    });

    expect(result.current.accounts).toEqual([]);
    expect(dependencies.loadRuntimeStatuses).not.toHaveBeenCalled();
  });

  it("uses canonical demo fixtures without provider I/O", async () => {
    const dependencies = testDependencies();
    const { result } = renderController(dependencies, {
      authenticated: true,
      demo: true,
      runtimePollingEnabled: true,
    });

    expect(result.current.accounts.map((item) => item.provider)).toEqual(["KORAIL", "SRT"]);
    expect(result.current.accounts.every((item) => item.loginMethod === null)).toBe(true);
    expect(result.current.runtimeStatuses.map((item) => item.provider)).toEqual(["KORAIL", "SRT"]);
    expect(result.current.accountAuthStatusFor("KORAIL")).toBe("authenticated");
    expect(dependencies.loadAccounts).not.toHaveBeenCalled();
    expect(dependencies.loadRuntimeStatuses).not.toHaveBeenCalled();
  });

  it("refreshes accounts and runtime after a provider authentication transition", async () => {
    const dependencies = testDependencies();
    const initial = account("KORAIL", { lastAuthStatus: "auth_required" });
    const refreshed = account("KORAIL", { lastAuthStatus: "authenticated" });
    const pendingRefresh = deferred<ReadonlyArray<ProviderAccount>>();
    dependencies.loadAccounts
      .mockResolvedValueOnce([initial])
      .mockReturnValueOnce(pendingRefresh.promise);
    dependencies.loadRuntimeStatuses.mockResolvedValue([runtimeStatus("KORAIL")]);
    const { result } = renderController(dependencies);
    await waitFor(() => expect(result.current.accountAuthStatusFor("KORAIL"))
      .toBe("auth_required"));
    dependencies.loadRuntimeStatuses.mockClear();

    act(() => result.current.onProviderAuthenticationTransition());

    expect(result.current.loading).toBe(false);
    expect(result.current.accountAuthStatusFor("KORAIL")).toBeNull();
    await act(async () => {
      pendingRefresh.resolve([refreshed]);
      await pendingRefresh.promise;
    });
    await waitFor(() => expect(dependencies.loadRuntimeStatuses).toHaveBeenCalledOnce());
    expect(result.current.accounts).toEqual([refreshed]);
    expect(result.current.accountAuthStatusFor("KORAIL")).toBe("authenticated");
  });

  it("keeps a failed provider authentication transition fail-closed without a toast", async () => {
    const dependencies = testDependencies();
    const initial = account("KORAIL");
    dependencies.loadAccounts
      .mockResolvedValueOnce([initial])
      .mockRejectedValueOnce(new Error("transition failed"));
    const { result, pushToast } = renderController(dependencies);
    await waitFor(() => expect(result.current.accountAuthStatusFor("KORAIL"))
      .toBe("authenticated"));
    pushToast.mockClear();

    act(() => result.current.onProviderAuthenticationTransition());

    await waitFor(() => expect(dependencies.loadAccounts).toHaveBeenCalledTimes(2));
    expect(result.current.accounts).toEqual([initial]);
    expect(result.current.accountAuthStatusFor("KORAIL")).toBeNull();
    expect(result.current.loading).toBe(false);
    expect(pushToast).not.toHaveBeenCalled();
  });

  it("polls runtime status immediately and every 15 seconds until disabled", async () => {
    const dependencies = testDependencies();
    dependencies.loadAccounts.mockResolvedValue([account("KORAIL")]);
    dependencies.loadRuntimeStatuses.mockResolvedValue([runtimeStatus("KORAIL")]);
    const { rerender, unmount } = renderController(dependencies);
    await waitFor(() => expect(dependencies.loadRuntimeStatuses).toHaveBeenCalledOnce());
    dependencies.loadRuntimeStatuses.mockClear();
    vi.useFakeTimers();

    rerender({ ...defaultHookProps, runtimePollingEnabled: true });
    await act(async () => undefined);
    expect(dependencies.loadRuntimeStatuses).toHaveBeenCalledOnce();

    await act(async () => vi.advanceTimersByTimeAsync(15_000));
    expect(dependencies.loadRuntimeStatuses).toHaveBeenCalledTimes(2);

    rerender({ ...defaultHookProps, runtimePollingEnabled: false });
    await act(async () => vi.advanceTimersByTimeAsync(30_000));
    expect(dependencies.loadRuntimeStatuses).toHaveBeenCalledTimes(2);

    unmount();
    await act(async () => vi.advanceTimersByTimeAsync(30_000));
    expect(dependencies.loadRuntimeStatuses).toHaveBeenCalledTimes(2);
  });

  it("preserves the latest runtime status when polling fails", async () => {
    const dependencies = testDependencies();
    const latest = runtimeStatus("KORAIL");
    dependencies.loadRuntimeStatuses
      .mockResolvedValueOnce([latest])
      .mockRejectedValueOnce(new Error("poll failed"));
    const { result, rerender, pushToast } = renderController(dependencies);
    await waitFor(() => expect(result.current.runtimeStatuses).toEqual([latest]));

    rerender({ ...defaultHookProps, runtimePollingEnabled: true });
    await waitFor(() => expect(dependencies.loadRuntimeStatuses).toHaveBeenCalledTimes(2));

    expect(result.current.runtimeStatuses).toEqual([latest]);
    expect(pushToast).not.toHaveBeenCalled();
  });

  it("saves a live account at the front, collapses its provider, and refreshes runtime", async () => {
    const dependencies = testDependencies();
    const oldKorail = account("KORAIL", { credentialVersion: 1 });
    const duplicateKorail = account("KORAIL", { credentialVersion: 2 });
    const srt = account("SRT");
    const saved = account("KORAIL", { credentialVersion: 3, maskedLoginId: "56***" });
    const pendingSave = deferred<ProviderAccount>();
    dependencies.loadAccounts.mockResolvedValue([srt, oldKorail, duplicateKorail]);
    dependencies.persistAccount.mockReturnValue(pendingSave.promise);
    const { result, pushToast } = renderController(dependencies);
    await waitFor(() => expect(result.current.accounts).toHaveLength(3));
    await waitFor(() => expect(dependencies.loadRuntimeStatuses).toHaveBeenCalledOnce());
    dependencies.loadRuntimeStatuses.mockClear();

    let savePromise = Promise.resolve();
    act(() => {
      savePromise = result.current.saveAccount("KORAIL", credentialInput);
    });
    expect(result.current.pendingProvider).toBe("KORAIL");
    expect(dependencies.persistAccount).toHaveBeenCalledWith("KORAIL", credentialInput);

    await act(async () => {
      pendingSave.resolve(saved);
      await savePromise;
    });

    expect(result.current.accounts).toEqual([saved, srt]);
    expect(result.current.pendingProvider).toBeNull();
    expect(dependencies.loadRuntimeStatuses).toHaveBeenCalledOnce();
    expect(pushToast).toHaveBeenLastCalledWith("KORAIL 철도 계정을 저장했습니다.");
  });

  it("saves a masked demo account locally and refreshes demo runtime locally", async () => {
    const dependencies = testDependencies();
    const { result, pushToast } = renderController(dependencies, {
      authenticated: true,
      demo: true,
      runtimePollingEnabled: true,
    });
    await act(async () => undefined);

    await act(async () => result.current.saveAccount("SRT", {
      ...credentialInput,
      loginId: "abcdef",
    }));

    expect(result.current.accounts[0]).toMatchObject({
      provider: "SRT",
      loginMethod: null,
      maskedLoginId: "ab***",
      updatedAt: "2026-08-05T12:00:00Z",
    });
    expect(dependencies.persistAccount).not.toHaveBeenCalled();
    expect(dependencies.loadRuntimeStatuses).not.toHaveBeenCalled();
    expect(pushToast).toHaveBeenLastCalledWith("SRT 철도 계정을 저장했습니다.");
  });

  it("rethrows a live save failure and always clears pending state", async () => {
    const dependencies = testDependencies();
    dependencies.persistAccount.mockRejectedValue("malformed failure");
    const { result, pushToast } = renderController(dependencies);
    await waitFor(() => expect(dependencies.loadAccounts).toHaveBeenCalledOnce());

    await expect(result.current.saveAccount("KORAIL", credentialInput))
      .rejects.toBe("malformed failure");

    expect(result.current.pendingProvider).toBeNull();
    expect(pushToast).toHaveBeenLastCalledWith("철도 계정을 저장하지 못했습니다.");
  });

  it("deletes a live account by preserving login method and resetting exact fields", async () => {
    const dependencies = testDependencies();
    const korail = account("KORAIL", { loginMethod: "email", credentialVersion: 8 });
    const srt = account("SRT");
    const pendingDelete = deferred<void>();
    dependencies.loadAccounts.mockResolvedValue([korail, srt]);
    dependencies.removeAccount.mockReturnValue(pendingDelete.promise);
    const { result, pushToast } = renderController(dependencies);
    await waitFor(() => expect(result.current.accounts).toEqual([korail, srt]));
    await waitFor(() => expect(dependencies.loadRuntimeStatuses).toHaveBeenCalledOnce());
    dependencies.loadRuntimeStatuses.mockClear();

    let deletePromise = Promise.resolve();
    act(() => {
      deletePromise = result.current.deleteAccount("KORAIL");
    });
    expect(result.current.pendingProvider).toBe("KORAIL");
    expect(dependencies.removeAccount).toHaveBeenCalledWith("KORAIL");

    await act(async () => {
      pendingDelete.resolve(undefined);
      await deletePromise;
    });

    expect(result.current.accounts).toEqual([{
      ...korail,
      configured: false,
      enabled: false,
      maskedLoginId: null,
      credentialVersion: 0,
      lastAuthStatus: "not_checked",
      lastAuthenticatedAt: null,
      updatedAt: null,
    }, srt]);
    expect(result.current.accounts[0]?.loginMethod).toBe("email");
    expect(result.current.pendingProvider).toBeNull();
    expect(dependencies.loadRuntimeStatuses).toHaveBeenCalledOnce();
    expect(pushToast).toHaveBeenLastCalledWith("KORAIL 철도 계정 연결을 해제했습니다.");
  });

  it("deletes a demo account locally without calling provider APIs", async () => {
    const dependencies = testDependencies();
    const { result } = renderController(dependencies, {
      authenticated: true,
      demo: true,
      runtimePollingEnabled: true,
    });
    await act(async () => undefined);

    await act(async () => result.current.deleteAccount("SRT"));

    expect(result.current.accounts.find((item) => item.provider === "SRT")).toMatchObject({
      configured: false,
      enabled: false,
      loginMethod: null,
      maskedLoginId: null,
      credentialVersion: 0,
      lastAuthStatus: "not_checked",
      lastAuthenticatedAt: null,
      updatedAt: null,
    });
    expect(dependencies.removeAccount).not.toHaveBeenCalled();
    expect(dependencies.loadRuntimeStatuses).not.toHaveBeenCalled();
  });

  it("rethrows a delete failure without changing accounts and clears pending state", async () => {
    const dependencies = testDependencies();
    const initial = account("KORAIL");
    dependencies.loadAccounts.mockResolvedValue([initial]);
    const failure = new Error("delete failed");
    dependencies.removeAccount.mockRejectedValue(failure);
    const { result, pushToast } = renderController(dependencies);
    await waitFor(() => expect(result.current.accounts).toEqual([initial]));

    await expect(result.current.deleteAccount("KORAIL")).rejects.toBe(failure);

    expect(result.current.accounts).toEqual([initial]);
    expect(result.current.pendingProvider).toBeNull();
    expect(pushToast).toHaveBeenLastCalledWith("delete failed");
  });

  it("projects account authentication status with fail-closed provider rules", async () => {
    const dependencies = testDependencies();
    dependencies.loadAccounts.mockResolvedValue([
      account("KORAIL", { lastAuthStatus: "provider_blocked" }),
      account("SRT", { enabled: false, lastAuthStatus: "authenticated" }),
    ]);
    const { result } = renderController(dependencies);

    expect(result.current.accountAuthStatusFor("KORAIL")).toBeNull();
    await waitFor(() => expect(result.current.accountAuthStatusFor("KORAIL"))
      .toBe("provider_blocked"));
    expect(result.current.accountAuthStatusFor("SRT")).toBe("not_checked");
    expect(result.current.accountAuthStatusFor("MOCK")).toBeNull();
    expect(result.current.accountAuthStatusFor("UNKNOWN")).toBeNull();
  });

  it("resets every provider-account-owned state", async () => {
    const dependencies = testDependencies();
    dependencies.loadAccounts.mockResolvedValue([account("KORAIL")]);
    dependencies.loadRuntimeStatuses.mockResolvedValue([runtimeStatus("KORAIL")]);
    const { result } = renderController(dependencies);
    await waitFor(() => expect(result.current.runtimeStatuses).toHaveLength(1));

    act(() => result.current.reset());

    expect(result.current.accounts).toEqual([]);
    expect(result.current.runtimeStatuses).toEqual([]);
    expect(result.current.loading).toBe(false);
    expect(result.current.pendingProvider).toBeNull();
    expect(result.current.accountAuthStatusFor("KORAIL")).toBeNull();
  });
});
