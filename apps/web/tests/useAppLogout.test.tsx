import { act, renderHook } from "@testing-library/react";
import type { Dispatch, SetStateAction } from "react";
import { describe, expect, it, vi } from "vitest";

import type { WatchReadModel } from "../src/api/watches";
import {
  useAppLogout,
  type UseAppLogoutOptions,
} from "../src/app/useAppLogout";

interface LogoutHarness {
  events: string[];
  committedValues: Array<SetStateAction<ReadonlyArray<WatchReadModel>>>;
  logoutRequest: ReturnType<typeof vi.fn<() => Promise<unknown>>>;
  options: UseAppLogoutOptions;
}

function logoutHarness(
  demo: boolean,
  request: () => Promise<unknown> = async () => null,
): LogoutHarness {
  const events: string[] = [];
  const committedValues: Array<SetStateAction<ReadonlyArray<WatchReadModel>>> = [];
  const logoutRequest = vi.fn<() => Promise<unknown>>(async () => {
    events.push("logoutRequest");
    return request();
  });
  const commitWatches = vi.fn<Dispatch<SetStateAction<ReadonlyArray<WatchReadModel>>>>(
    (value) => {
      events.push("commitWatches");
      committedValues.push(value);
    },
  );
  const step = (name: string): (() => void) => vi.fn(() => {
    events.push(name);
  });

  return {
    events,
    committedValues,
    logoutRequest,
    options: {
      demo,
      commitWatches,
      resetNotificationChannels: step("resetNotificationChannels"),
      resetProviderAccounts: step("resetProviderAccounts"),
      resetUiPreferences: step("resetUiPreferences"),
      clearNotifications: step("clearNotifications"),
      markUnauthenticated: step("markUnauthenticated"),
      logoutRequest,
    },
  };
}

const cleanupOrder = [
  "commitWatches",
  "resetNotificationChannels",
  "resetProviderAccounts",
  "resetUiPreferences",
  "clearNotifications",
  "markUnauthenticated",
];

describe("useAppLogout", () => {
  it("awaits live logout and then performs every cleanup in exact order", async () => {
    let finishRequest: (() => void) | undefined;
    const request = new Promise<void>((resolve) => {
      finishRequest = resolve;
    });
    const harness = logoutHarness(false, () => request);
    const { result } = renderHook(() => useAppLogout(harness.options));

    let logoutPromise = Promise.resolve();
    act(() => {
      logoutPromise = result.current();
    });
    expect(harness.events).toEqual(["logoutRequest"]);
    expect(harness.committedValues).toEqual([]);

    await act(async () => {
      finishRequest?.();
      await logoutPromise;
    });

    expect(harness.events).toEqual(["logoutRequest", ...cleanupOrder]);
    expect(harness.committedValues).toEqual([[]]);
    expect(harness.logoutRequest).toHaveBeenCalledOnce();
  });

  it("skips the remote request in demo and still performs exact cleanup", async () => {
    const harness = logoutHarness(true);
    const { result } = renderHook(() => useAppLogout(harness.options));

    await act(async () => result.current());

    expect(harness.logoutRequest).not.toHaveBeenCalled();
    expect(harness.events).toEqual(cleanupOrder);
    expect(harness.committedValues).toEqual([[]]);
  });

  it("performs exact cleanup after live logout failure and rethrows the original reason", async () => {
    const failure = new Error("logout failed");
    const harness = logoutHarness(false, async () => {
      throw failure;
    });
    const { result } = renderHook(() => useAppLogout(harness.options));

    let caught: unknown;
    await act(async () => {
      try {
        await result.current();
      } catch (reason) {
        caught = reason;
      }
    });

    expect(caught).toBe(failure);
    expect(harness.events).toEqual(["logoutRequest", ...cleanupOrder]);
    expect(harness.committedValues).toEqual([[]]);
    expect(harness.logoutRequest).toHaveBeenCalledOnce();
  });

  it("keeps the logout callback identity stable while dependencies are unchanged", () => {
    const harness = logoutHarness(false);
    const { result, rerender } = renderHook(() => useAppLogout(harness.options));
    const initialLogout = result.current;

    rerender();

    expect(result.current).toBe(initialLogout);
  });
});
