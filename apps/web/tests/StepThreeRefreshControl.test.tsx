import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { StepThreeRefreshControl } from "../src/features/new-wait/StepThreeRefreshControl";

const originalVisibilityDescriptor = Object.getOwnPropertyDescriptor(document, "visibilityState");

afterEach(() => {
  vi.useRealTimers();
  if (originalVisibilityDescriptor) {
    Object.defineProperty(document, "visibilityState", originalVisibilityDescriptor);
  }
});

describe("StepThreeRefreshControl", () => {
  it("uses the default five-second interval for cache-only automatic refreshes", async () => {
    vi.useFakeTimers();
    const onAutomaticRefresh = vi.fn(async () => undefined);
    const onManualRefresh = vi.fn(async () => undefined);
    render(<StepThreeRefreshControl onAutomaticRefresh={onAutomaticRefresh} onManualRefresh={onManualRefresh} />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_999);
    });
    expect(onAutomaticRefresh).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(onAutomaticRefresh).toHaveBeenCalledOnce();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(800);
    });

    fireEvent.click(screen.getByRole("button", { name: "시간표 새로고침" }));
    expect(onManualRefresh).toHaveBeenCalledOnce();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800);
    });
    expect(screen.getByRole("status").textContent).toMatch(/^최근 갱신 \d{2}:\d{2}:\d{2}$/);
  });

  it("keeps the icon visibly rotating for one full turn without replacing the stable timestamp", async () => {
    vi.useFakeTimers();
    const onManualRefresh = vi.fn(async () => undefined);
    render(<StepThreeRefreshControl onAutomaticRefresh={async () => undefined} onManualRefresh={onManualRefresh} />);

    const button = screen.getByRole("button", { name: "시간표 새로고침" });
    const status = screen.getByRole("status");
    const initialStatus = status.textContent;
    fireEvent.click(button);
    await act(async () => Promise.resolve());

    expect(onManualRefresh).toHaveBeenCalledOnce();
    expect(button.hasAttribute("disabled")).toBe(false);
    expect(button.getAttribute("aria-busy")).toBe("true");
    expect(button.querySelector(".refresh-icon")?.classList.contains("is-spinning")).toBe(true);
    expect(status.textContent).toBe(initialStatus);

    fireEvent.click(button);
    expect(onManualRefresh).toHaveBeenCalledOnce();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(799);
    });
    expect(button.getAttribute("aria-busy")).toBe("true");
    expect(status.textContent).toBe(initialStatus);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(button.getAttribute("aria-busy")).toBe("false");
    expect(button.querySelector(".refresh-icon")?.classList.contains("is-spinning")).toBe(false);
    expect(status.textContent).not.toBe(initialStatus);
  });

  it("stops its timer while the document is hidden and starts a fresh interval when visible", async () => {
    vi.useFakeTimers();
    setVisibility("hidden");
    const onAutomaticRefresh = vi.fn(async () => undefined);
    render(<StepThreeRefreshControl onAutomaticRefresh={onAutomaticRefresh} onManualRefresh={async () => undefined} intervalSeconds={2} />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(onAutomaticRefresh).not.toHaveBeenCalled();

    setVisibility("visible");
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_999);
    });
    expect(onAutomaticRefresh).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(onAutomaticRefresh).toHaveBeenCalledOnce();
  });

  it("does not start an automatic refresh while a manual refresh is in flight", async () => {
    vi.useFakeTimers();
    let resolveManual: (() => void) | undefined;
    const onManualRefresh = vi.fn(() => new Promise<void>((resolve) => { resolveManual = resolve; }));
    const onAutomaticRefresh = vi.fn(async () => undefined);
    render(<StepThreeRefreshControl onAutomaticRefresh={onAutomaticRefresh} onManualRefresh={onManualRefresh} />);

    const button = screen.getByRole("button", { name: "시간표 새로고침" });
    fireEvent.click(button);
    expect(button.hasAttribute("disabled")).toBe(false);
    expect(button.getAttribute("aria-busy")).toBe("true");
    fireEvent.click(button);
    expect(onManualRefresh).toHaveBeenCalledOnce();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(onAutomaticRefresh).not.toHaveBeenCalled();

    await act(async () => {
      resolveManual?.();
      await vi.advanceTimersByTimeAsync(800);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_500);
    });
    expect(onAutomaticRefresh).toHaveBeenCalledOnce();
  });
});

function setVisibility(value: "hidden" | "visible"): void {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value,
  });
}
