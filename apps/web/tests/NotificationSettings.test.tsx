import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Settings } from "../src/App.jsx";

const preferences = {
  timetableRefreshIntervalSeconds: 5,
  seatObservationIntervalSeconds: 5,
  updatedAt: "2026-07-31T00:00:00Z",
};

function renderSettings(overrides: Record<string, unknown> = {}) {
  const props = {
    channels: [],
    demo: false,
    uiPreferences: preferences,
    savingUiPreferences: false,
    onSaveUiPreferences: vi.fn(),
    onSaveChannel: vi.fn().mockResolvedValue(undefined),
    onToggleChannel: vi.fn().mockResolvedValue(undefined),
    onTestChannel: vi.fn().mockResolvedValue(undefined),
    onConnectWebPush: vi.fn().mockResolvedValue(undefined),
    onLogout: vi.fn(),
    ...overrides,
  };
  render(<Settings {...props} />);
  return props;
}

describe("notification channel settings", () => {
  it("starts Web Push setup from an unconfigured switch and only disables it while pending", async () => {
    const user = userEvent.setup();
    let finishSetup: (() => void) | undefined;
    const onConnectWebPush = vi.fn(() => new Promise<void>((resolve) => {
      finishSetup = resolve;
    }));
    renderSettings({ onConnectWebPush });

    const toggle = screen.getByRole("checkbox", { name: "OS 알림 켜기" });
    expect((toggle as HTMLInputElement).disabled).toBe(false);
    await user.click(toggle);

    expect(onConnectWebPush).toHaveBeenCalledOnce();
    expect((toggle as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText("처리 중…")).toBeTruthy();
    finishSetup?.();
    await waitFor(() => expect((toggle as HTMLInputElement).disabled).toBe(false));
  });

  it("opens configuration from an unconfigured Telegram switch", async () => {
    const user = userEvent.setup();
    renderSettings();

    const toggle = screen.getByRole("checkbox", { name: "텔레그램 켜기" });
    expect((toggle as HTMLInputElement).disabled).toBe(false);
    await user.click(toggle);

    expect(screen.getByRole("heading", { name: "텔레그램 연결" })).toBeTruthy();
    expect(screen.getByLabelText("Bot token")).toBeTruthy();
    expect(screen.getByLabelText("Chat ID")).toBeTruthy();
  });

  it("toggles and tests an existing channel with accessible controls", async () => {
    const user = userEvent.setup();
    const channel = { id: "telegram-1", kind: "telegram", name: "가족 알림", enabled: true };
    const onToggleChannel = vi.fn().mockResolvedValue(undefined);
    const onTestChannel = vi.fn().mockResolvedValue(undefined);
    renderSettings({ channels: [channel], onToggleChannel, onTestChannel });

    await user.click(screen.getByRole("checkbox", { name: "텔레그램 끄기" }));
    expect(onToggleChannel).toHaveBeenCalledWith(channel, false);
    await user.click(screen.getByRole("button", { name: "텔레그램 시험 알림 보내기" }));
    expect(onTestChannel).toHaveBeenCalledWith(channel);
  });

  it("shows when browser permission is blocked and prevents a misleading enabled switch", () => {
    renderSettings({
      channels: [{ id: "push-1", kind: "web_push", name: "내 PC", enabled: true }],
      browserPushState: { support: "supported", permission: "denied", subscribed: false },
    });

    expect(screen.getByText("브라우저 사이트 설정에서 알림 권한이 차단됨")).toBeTruthy();
    expect(screen.getByText(/브라우저를 닫아도 운영체제 알림 영역/)).toBeTruthy();
    expect((screen.getByRole("checkbox", { name: "OS 알림 켜기" }) as HTMLInputElement).checked).toBe(false);
    expect((screen.getByRole("button", { name: "OS 알림 시험 알림 보내기" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
