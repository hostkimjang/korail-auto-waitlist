import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  BrowserPushState,
  NotificationChannel,
} from "../src/api/notifications";
import { WebPushEnrollmentPrompt } from "../src/features/settings/WebPushEnrollmentPrompt";

const defaultState: BrowserPushState = {
  support: "supported",
  permission: "default",
  subscribed: false,
  deviceKey: null,
};

function webPushChannel(overrides: Partial<NotificationChannel> = {}): NotificationChannel {
  return {
    id: "web-push-one",
    kind: "web_push",
    name: "이 기기",
    enabled: true,
    configured: true,
    deviceKey: "device-one",
    activeDeviceCount: 1,
    createdAt: "2026-08-08T00:00:00Z",
    updatedAt: "2026-08-08T00:00:00Z",
    ...overrides,
  };
}

describe("WebPushEnrollmentPrompt", () => {
  it("connects OS notifications from every app view without opening settings", async () => {
    const onConnect = vi.fn().mockResolvedValue(undefined);
    render(
      <WebPushEnrollmentPrompt
        browserPushState={defaultState}
        channels={[]}
        channelsLoaded
        demo={false}
        suppressed={false}
        onConnect={onConnect}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "OS 알림 켜기" }));

    expect(onConnect).toHaveBeenCalledOnce();
    await waitFor(() => expect(
      (screen.getByRole("button", { name: "OS 알림 켜기" }) as HTMLButtonElement).disabled,
    ).toBe(false));
  });

  it("stays hidden for a connected current device or an explicit app opt-out", () => {
    const { rerender } = render(
      <WebPushEnrollmentPrompt
        browserPushState={{
          support: "supported",
          permission: "granted",
          subscribed: true,
          deviceKey: "device-one",
        }}
        channels={[webPushChannel()]}
        channelsLoaded
        demo={false}
        suppressed={false}
        onConnect={vi.fn()}
      />,
    );
    expect(screen.queryByRole("region", { name: "OS 알림 연결" })).toBeNull();

    rerender(
      <WebPushEnrollmentPrompt
        browserPushState={{ ...defaultState, permission: "granted" }}
        channels={[]}
        channelsLoaded
        demo={false}
        suppressed
        onConnect={vi.fn()}
      />,
    );
    expect(screen.queryByRole("region", { name: "OS 알림 연결" })).toBeNull();
  });

  it("explains a browser-level block without offering an ineffective retry", () => {
    render(
      <WebPushEnrollmentPrompt
        browserPushState={{ ...defaultState, permission: "denied" }}
        channels={[]}
        channelsLoaded
        demo={false}
        suppressed={false}
        onConnect={vi.fn()}
      />,
    );

    expect(screen.getByRole("region", { name: "OS 알림 연결" }).textContent).toContain(
      "브라우저의 이 사이트 권한에서 알림을 허용해야",
    );
    expect(screen.queryByRole("button")).toBeNull();
  });
});
