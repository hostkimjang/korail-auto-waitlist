import { describe, expect, it, vi } from "vitest";

import {
  mapPwaNotificationHint,
  subscribeToPwaNotificationHints,
} from "../src/features/app/pwaNotificationBridge";

class FakeMessageTarget {
  readonly addEventListener = vi.fn(
    (_type: "message", listener: (event: MessageEvent<unknown>) => void) => {
      this.listener = listener;
    },
  );

  readonly removeEventListener = vi.fn(
    (_type: "message", listener: (event: MessageEvent<unknown>) => void) => {
      if (this.listener === listener) this.listener = undefined;
    },
  );

  private listener: ((event: MessageEvent<unknown>) => void) | undefined;

  emit(data: unknown): void {
    this.listener?.({ data } as MessageEvent<unknown>);
  }
}

describe("PWA notification message bridge", () => {
  it("accepts only a minimal typed, non-secret notification hint", () => {
    expect(mapPwaNotificationHint({
      type: "railwait:notification",
      kind: "push",
      watchId: " watch-one ",
      status: "seat_found",
      message: "must not cross the boundary",
      official_booking_url: "https://example.invalid",
    })).toEqual({
      type: "railwait:notification",
      kind: "push",
      watchId: "watch-one",
      status: "seat_found",
    });
    expect(mapPwaNotificationHint({
      type: "railwait:notification",
      kind: "push",
      watchId: "watch-one",
      status: "not-a-watch-status",
    })).toBeNull();
    expect(mapPwaNotificationHint({
      type: "other-app",
      kind: "push",
      watchId: "watch-one",
      status: "seat_found",
    })).toBeNull();
  });

  it("registers one listener, ignores malformed messages, and removes that listener", () => {
    const target = new FakeMessageTarget();
    const onHint = vi.fn();
    const unsubscribe = subscribeToPwaNotificationHints(onHint, target);

    target.emit({ type: "railwait:notification", kind: "push", status: "seat_found" });
    target.emit({
      type: "railwait:notification",
      kind: "click",
      watchId: "watch-one",
      status: "seat_found",
    });

    expect(target.addEventListener).toHaveBeenCalledOnce();
    expect(onHint).toHaveBeenCalledOnce();
    unsubscribe();
    expect(target.removeEventListener).toHaveBeenCalledWith("message", expect.any(Function));
    target.emit({
      type: "railwait:notification",
      kind: "push",
      watchId: "watch-two",
      status: "payment_required",
    });
    expect(onHint).toHaveBeenCalledOnce();
  });
});
