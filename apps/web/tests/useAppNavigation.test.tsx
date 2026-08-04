import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAppNavigation } from "../src/app/useAppNavigation";

describe("useAppNavigation", () => {
  beforeEach(() => {
    vi.mocked(window.scrollTo).mockClear();
  });

  it("starts on home with notification settings as both section states", () => {
    const { result } = renderHook(() => useAppNavigation());

    expect(result.current.activeView).toBe("home");
    expect(result.current.settingsInitialSection).toBe("notifications");
    expect(result.current.settingsActiveSection).toBe("notifications");
  });

  it("navigates between non-settings views without changing settings state", () => {
    const { result } = renderHook(() => useAppNavigation());
    act(() => result.current.onSettingsSectionChange("display"));

    act(() => result.current.navigate("new"));
    expect(result.current.activeView).toBe("new");
    expect(result.current.settingsInitialSection).toBe("notifications");
    expect(result.current.settingsActiveSection).toBe("display");

    act(() => result.current.navigate("reservations"));
    expect(result.current.activeView).toBe("reservations");
    expect(result.current.settingsInitialSection).toBe("notifications");
    expect(result.current.settingsActiveSection).toBe("display");
  });

  it("defaults both settings section states when no section is supplied", () => {
    const { result } = renderHook(() => useAppNavigation());
    act(() => result.current.onSettingsSectionChange("security"));

    act(() => result.current.navigate("settings"));

    expect(result.current.activeView).toBe("settings");
    expect(result.current.settingsInitialSection).toBe("notifications");
    expect(result.current.settingsActiveSection).toBe("notifications");
  });

  it("sets both settings section states from an explicit destination", () => {
    const { result } = renderHook(() => useAppNavigation());

    act(() => result.current.navigate("settings", "rail-accounts"));

    expect(result.current.activeView).toBe("settings");
    expect(result.current.settingsInitialSection).toBe("rail-accounts");
    expect(result.current.settingsActiveSection).toBe("rail-accounts");
  });

  it("updates only the active section for an internal settings change", () => {
    const { result } = renderHook(() => useAppNavigation());
    act(() => result.current.navigate("settings", "rail-accounts"));

    act(() => result.current.onSettingsSectionChange("system"));

    expect(result.current.settingsInitialSection).toBe("rail-accounts");
    expect(result.current.settingsActiveSection).toBe("system");
  });

  it("smooth-scrolls exactly once for every navigation", () => {
    const { result } = renderHook(() => useAppNavigation());

    act(() => result.current.navigate("home"));
    expect(window.scrollTo).toHaveBeenCalledOnce();
    expect(window.scrollTo).toHaveBeenLastCalledWith({ top: 0, behavior: "smooth" });

    act(() => result.current.navigate("settings", "display"));
    expect(window.scrollTo).toHaveBeenCalledTimes(2);
    expect(window.scrollTo).toHaveBeenLastCalledWith({ top: 0, behavior: "smooth" });
  });

  it("keeps navigation callback identities stable across rerenders", () => {
    const { result, rerender } = renderHook(() => useAppNavigation());
    const initialNavigate = result.current.navigate;
    const initialSectionChange = result.current.onSettingsSectionChange;

    rerender();

    expect(result.current.navigate).toBe(initialNavigate);
    expect(result.current.onSettingsSectionChange).toBe(initialSectionChange);
  });
});
