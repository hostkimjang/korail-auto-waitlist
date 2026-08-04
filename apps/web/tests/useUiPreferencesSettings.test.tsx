import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  UiPreferences,
  UpdateUiPreferencesInput,
} from "../src/api/uiPreferences";
import {
  useUiPreferencesSettings,
  type UiPreferencesLoader,
  type UiPreferencesPersister,
} from "../src/features/settings/useUiPreferencesSettings";

interface HookProps {
  authenticated: boolean;
  demo: boolean;
}

const defaultHookProps: HookProps = {
  authenticated: true,
  demo: false,
};

const defaultPreferences: UiPreferences = {
  timetableRefreshIntervalSeconds: 5,
  seatObservationIntervalSeconds: 5,
  updatedAt: "1970-01-01T00:00:00.000Z",
};

const savedPreferences: UiPreferences = {
  timetableRefreshIntervalSeconds: 45,
  seatObservationIntervalSeconds: 10,
  updatedAt: "2026-08-05T00:00:00Z",
};

const saveInput: UpdateUiPreferencesInput = {
  timetableRefreshIntervalSeconds: 45,
  seatObservationIntervalSeconds: 10,
};

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
  const loadPreferences = vi.fn<UiPreferencesLoader>()
    .mockResolvedValue(savedPreferences);
  const persistPreferences = vi.fn<UiPreferencesPersister>()
    .mockResolvedValue(savedPreferences);
  const now = vi.fn<() => string>().mockReturnValue("2026-08-05T12:00:00Z");
  return { loadPreferences, persistPreferences, now };
}

function renderController(
  dependencies: ReturnType<typeof testDependencies>,
  initialProps: HookProps = defaultHookProps,
) {
  const pushToast = vi.fn<(message: string) => void>();
  const hook = renderHook((props: HookProps) => useUiPreferencesSettings({
    ...props,
    pushToast,
    ...dependencies,
  }), { initialProps });
  return { ...hook, pushToast };
}

describe("useUiPreferencesSettings", () => {
  it("starts with canonical five-second defaults", () => {
    const dependencies = testDependencies();
    const { result } = renderController(dependencies, {
      authenticated: false,
      demo: false,
    });

    expect(result.current.preferences).toEqual(defaultPreferences);
    expect(result.current.saving).toBe(false);
  });

  it("does not load preferences before authentication", () => {
    const dependencies = testDependencies();
    renderController(dependencies, { authenticated: false, demo: false });

    expect(dependencies.loadPreferences).not.toHaveBeenCalled();
  });

  it("keeps demo preferences local without an initial request", () => {
    const dependencies = testDependencies();
    const { result } = renderController(dependencies, {
      authenticated: true,
      demo: true,
    });

    expect(result.current.preferences).toEqual(defaultPreferences);
    expect(dependencies.loadPreferences).not.toHaveBeenCalled();
  });

  it("loads live preferences after authentication", async () => {
    const dependencies = testDependencies();
    const { result, pushToast } = renderController(dependencies);

    await waitFor(() => expect(result.current.preferences).toEqual(savedPreferences));

    expect(dependencies.loadPreferences).toHaveBeenCalledOnce();
    expect(pushToast).not.toHaveBeenCalled();
  });

  it("preserves defaults and reports an Error from initial loading", async () => {
    const dependencies = testDependencies();
    dependencies.loadPreferences.mockRejectedValue(new Error("설정 로드 실패"));
    const { result, pushToast } = renderController(dependencies);

    await waitFor(() => expect(pushToast).toHaveBeenCalledWith("설정 로드 실패"));

    expect(result.current.preferences).toEqual(defaultPreferences);
  });

  it("uses a safe fallback for an unknown initial loading failure", async () => {
    const dependencies = testDependencies();
    dependencies.loadPreferences.mockRejectedValue("malformed failure");
    const { result, pushToast } = renderController(dependencies);

    await waitFor(() => expect(pushToast).toHaveBeenCalledWith(
      "화면 갱신 설정을 불러오지 못했습니다.",
    ));

    expect(result.current.preferences).toEqual(defaultPreferences);
  });

  it("ignores a successful initial response after unmount", async () => {
    const dependencies = testDependencies();
    const pendingLoad = deferred<UiPreferences>();
    dependencies.loadPreferences.mockReturnValue(pendingLoad.promise);
    const { result, unmount } = renderController(dependencies);
    await waitFor(() => expect(dependencies.loadPreferences).toHaveBeenCalledOnce());

    unmount();
    await act(async () => {
      pendingLoad.resolve(savedPreferences);
      await pendingLoad.promise;
    });

    expect(result.current.preferences).toEqual(defaultPreferences);
  });

  it("ignores a failed initial response after unmount", async () => {
    const dependencies = testDependencies();
    const pendingLoad = deferred<UiPreferences>();
    dependencies.loadPreferences.mockReturnValue(pendingLoad.promise);
    const { unmount, pushToast } = renderController(dependencies);
    await waitFor(() => expect(dependencies.loadPreferences).toHaveBeenCalledOnce());

    unmount();
    await act(async () => {
      pendingLoad.reject(new Error("late failure"));
      await pendingLoad.promise.catch(() => undefined);
    });

    expect(pushToast).not.toHaveBeenCalled();
  });

  it("isolates a stale response across an authentication lifecycle", async () => {
    const dependencies = testDependencies();
    const staleLoad = deferred<UiPreferences>();
    const currentPreferences: UiPreferences = {
      timetableRefreshIntervalSeconds: 30,
      seatObservationIntervalSeconds: 3,
      updatedAt: "2026-08-05T01:00:00Z",
    };
    dependencies.loadPreferences
      .mockReturnValueOnce(staleLoad.promise)
      .mockResolvedValueOnce(currentPreferences);
    const { result, rerender } = renderController(dependencies);
    await waitFor(() => expect(dependencies.loadPreferences).toHaveBeenCalledOnce());

    rerender({ authenticated: false, demo: false });
    await act(async () => {
      staleLoad.resolve(savedPreferences);
      await staleLoad.promise;
    });
    expect(result.current.preferences).toEqual(defaultPreferences);

    rerender({ authenticated: true, demo: false });
    await waitFor(() => expect(result.current.preferences).toEqual(currentPreferences));
    expect(dependencies.loadPreferences).toHaveBeenCalledTimes(2);
  });

  it("saves live preferences with pending state, result, and success toast", async () => {
    const dependencies = testDependencies();
    const pendingSave = deferred<UiPreferences>();
    dependencies.persistPreferences.mockReturnValue(pendingSave.promise);
    const { result, pushToast } = renderController(dependencies, {
      authenticated: false,
      demo: false,
    });

    let savePromise = Promise.resolve(defaultPreferences);
    act(() => {
      savePromise = result.current.save(saveInput);
    });
    expect(result.current.saving).toBe(true);
    expect(dependencies.persistPreferences).toHaveBeenCalledWith(saveInput);

    let returned = defaultPreferences;
    await act(async () => {
      pendingSave.resolve(savedPreferences);
      returned = await savePromise;
    });

    expect(returned).toEqual(savedPreferences);
    expect(result.current.preferences).toEqual(savedPreferences);
    expect(result.current.saving).toBe(false);
    expect(pushToast).toHaveBeenLastCalledWith(
      "화면·좌석 관측 간격을 저장했습니다. 활성 작업의 다음 관측부터 적용됩니다.",
    );
  });

  it("saves demo preferences locally with the injected timestamp", async () => {
    const dependencies = testDependencies();
    const { result, pushToast } = renderController(dependencies, {
      authenticated: true,
      demo: true,
    });

    let returned = defaultPreferences;
    await act(async () => {
      returned = await result.current.save(saveInput);
    });

    const expected = { ...saveInput, updatedAt: "2026-08-05T12:00:00Z" };
    expect(returned).toEqual(expected);
    expect(result.current.preferences).toEqual(expected);
    expect(dependencies.persistPreferences).not.toHaveBeenCalled();
    expect(dependencies.now).toHaveBeenCalledOnce();
    expect(pushToast).toHaveBeenCalledOnce();
  });

  it("rethrows an Error from live saving and always clears saving state", async () => {
    const dependencies = testDependencies();
    const failure = new Error("설정 저장 실패");
    dependencies.persistPreferences.mockRejectedValue(failure);
    const { result, pushToast } = renderController(dependencies, {
      authenticated: false,
      demo: false,
    });

    await expect(result.current.save(saveInput)).rejects.toBe(failure);

    expect(result.current.preferences).toEqual(defaultPreferences);
    expect(result.current.saving).toBe(false);
    expect(pushToast).toHaveBeenLastCalledWith("설정 저장 실패");
  });

  it("rethrows an unknown live saving failure with a safe fallback", async () => {
    const dependencies = testDependencies();
    dependencies.persistPreferences.mockRejectedValue("malformed failure");
    const { result, pushToast } = renderController(dependencies, {
      authenticated: false,
      demo: false,
    });

    await expect(result.current.save(saveInput)).rejects.toBe("malformed failure");

    expect(result.current.saving).toBe(false);
    expect(pushToast).toHaveBeenLastCalledWith("화면·좌석 관측 간격을 저장하지 못했습니다.");
  });

  it("resets preferences and saving state to canonical defaults", async () => {
    const dependencies = testDependencies();
    const { result } = renderController(dependencies, {
      authenticated: true,
      demo: true,
    });
    await act(async () => result.current.save(saveInput));
    expect(result.current.preferences).not.toEqual(defaultPreferences);

    act(() => result.current.reset());

    expect(result.current.preferences).toEqual(defaultPreferences);
    expect(result.current.saving).toBe(false);
  });

  it("keeps save and reset callback identities stable across rerenders", () => {
    const dependencies = testDependencies();
    const { result, rerender } = renderController(dependencies, {
      authenticated: false,
      demo: false,
    });
    const initialSave = result.current.save;
    const initialReset = result.current.reset;

    rerender({ authenticated: true, demo: false });

    expect(result.current.save).toBe(initialSave);
    expect(result.current.reset).toBe(initialReset);
  });
});
