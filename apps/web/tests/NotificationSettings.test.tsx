import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type {
  BrowserPushState,
  NotificationChannel,
  NotificationChannelEditorSubmission,
} from "../src/api/notifications";
import { NotificationChannelSettings } from "../src/features/settings/NotificationChannelSettings";

const timestamp = "2026-08-05T00:00:00Z";

function channel(overrides: Partial<NotificationChannel> = {}): NotificationChannel {
  return {
    id: "telegram-1",
    kind: "telegram",
    name: "가족 알림",
    enabled: true,
    configured: true,
    deviceKey: null,
    activeDeviceCount: null,
    createdAt: timestamp,
    updatedAt: timestamp,
    ...overrides,
  };
}

function browserPushState(
  overrides: Partial<BrowserPushState> = {},
): BrowserPushState {
  return {
    support: "supported",
    permission: "default",
    subscribed: false,
    deviceKey: null,
    ...overrides,
  };
}

function renderSettings(overrides: Record<string, unknown> = {}) {
  const props = {
    channels: [] as NotificationChannel[],
    browserPushState: browserPushState(),
    onSaveChannel: vi.fn<(submission: NotificationChannelEditorSubmission) => Promise<void>>()
      .mockResolvedValue(undefined),
    onToggleChannel: vi.fn().mockResolvedValue(undefined),
    onTestChannel: vi.fn().mockResolvedValue(undefined),
    onConnectWebPush: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
  render(<NotificationChannelSettings {...props} />);
  return props;
}

describe("notification channel settings", () => {
  it("starts Web Push setup from an unconfigured switch and isolates its pending state", async () => {
    const user = userEvent.setup();
    let finishSetup: (() => void) | undefined;
    const onConnectWebPush = vi.fn(() => new Promise<void>((resolve) => {
      finishSetup = resolve;
    }));
    renderSettings({ onConnectWebPush });

    const pushToggle = screen.getByRole("checkbox", { name: "OS 알림 켜기" });
    const telegramToggle = screen.getByRole("checkbox", { name: "텔레그램 켜기" });
    expect(pushToggle.hasAttribute("aria-controls")).toBe(false);
    expect(screen.getByRole("button", { name: "OS 알림 연결 설정 열기" })
      .hasAttribute("aria-controls")).toBe(false);
    expect(screen.queryByRole("heading", { name: "모바일 팝업 알림 앱 연결" })).toBeNull();
    await user.click(pushToggle);

    expect(onConnectWebPush).toHaveBeenCalledOnce();
    expect((pushToggle as HTMLInputElement).disabled).toBe(true);
    expect((telegramToggle as HTMLInputElement).disabled).toBe(false);
    expect(pushToggle.closest(".setting-row")?.getAttribute("aria-busy")).toBe("true");
    expect(screen.getByText("처리 중…")).toBeTruthy();
    finishSetup?.();
    await waitFor(() => expect((pushToggle as HTMLInputElement).disabled).toBe(false));
    expect(pushToggle.closest(".setting-row")?.hasAttribute("aria-busy")).toBe(false);
  });

  it("keeps overlapping channel actions pending until each action finishes", async () => {
    const user = userEvent.setup();
    let finishPush: (() => void) | undefined;
    let finishTelegram: (() => void) | undefined;
    const onConnectWebPush = vi.fn(() => new Promise<void>((resolve) => {
      finishPush = resolve;
    }));
    const onToggleChannel = vi.fn(() => new Promise<void>((resolve) => {
      finishTelegram = resolve;
    }));
    renderSettings({
      channels: [channel()],
      onConnectWebPush,
      onToggleChannel,
    });

    const pushToggle = screen.getByRole("checkbox", { name: "OS 알림 켜기" });
    const telegramToggle = screen.getByRole("checkbox", { name: "텔레그램 끄기" });
    await user.click(pushToggle);
    await user.click(telegramToggle);

    expect((pushToggle as HTMLInputElement).disabled).toBe(true);
    expect((telegramToggle as HTMLInputElement).disabled).toBe(true);
    finishPush?.();
    await waitFor(() => expect((pushToggle as HTMLInputElement).disabled).toBe(false));
    expect((telegramToggle as HTMLInputElement).disabled).toBe(true);

    finishTelegram?.();
    await waitFor(() => expect((telegramToggle as HTMLInputElement).disabled).toBe(false));
  });

  it("opens Telegram configuration with empty non-hydrated secret fields", async () => {
    const user = userEvent.setup();
    renderSettings();

    await user.click(screen.getByRole("checkbox", { name: "텔레그램 켜기" }));
    expect(screen.getByRole("heading", { name: "텔레그램 연결" })).toBeTruthy();
    expect((screen.getByLabelText("Bot token") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Chat ID") as HTMLInputElement).value).toBe("");
  });

  it.each([
    {
      label: "텔레그램",
      inputs: [
        ["표시 이름", "업무 텔레그램"],
        ["Bot token", "secret-token"],
        ["Chat ID", "12345"],
      ],
      expected: {
        kind: "telegram",
        name: "업무 텔레그램",
        config: { bot_token: "secret-token", chat_id: "12345" },
      },
    },
    {
      label: "디스코드",
      inputs: [
        ["표시 이름", "업무 디스코드"],
        ["HTTPS URL", "https://discord.example/hook"],
      ],
      expected: {
        kind: "discord_webhook",
        name: "업무 디스코드",
        config: { url: "https://discord.example/hook" },
      },
    },
    {
      label: "범용 Webhook",
      inputs: [
        ["표시 이름", "업무 Webhook"],
        ["HTTPS URL", "https://hooks.example/rail"],
        ["Authorization (선택)", "Bearer secret"],
      ],
      expected: {
        kind: "generic_webhook",
        name: "업무 Webhook",
        config: { url: "https://hooks.example/rail", authorization: "Bearer secret" },
      },
    },
  ] as const)("submits the exact $label discriminated payload", async ({
    label,
    inputs,
    expected,
  }) => {
    const user = userEvent.setup();
    const onSaveChannel = vi.fn().mockResolvedValue(undefined);
    renderSettings({ onSaveChannel });

    await user.click(screen.getByRole("checkbox", { name: `${label} 켜기` }));
    for (const [inputLabel, value] of inputs) {
      await user.type(screen.getByLabelText(inputLabel), value);
    }
    if (label === "텔레그램") {
      const secret = screen.getByLabelText("Bot token");
      expect(secret.getAttribute("type")).toBe("password");
      expect(secret.getAttribute("autocomplete")).toBe("new-password");
      expect(secret.getAttribute("data-lpignore")).toBe("true");
    }
    await user.click(screen.getByRole("button", { name: "저장" }));

    expect(onSaveChannel).toHaveBeenCalledWith(expected);
    await waitFor(() => expect(screen.queryByRole("heading", { name: `${label} 연결` }))
      .toBeNull());
    await user.click(screen.getByRole("checkbox", { name: `${label} 켜기` }));
    for (const [inputLabel] of inputs) {
      expect((screen.getByLabelText(inputLabel) as HTMLInputElement).value).toBe("");
    }
  });

  it("keeps a failed secret draft for retry and clears it after cancel", async () => {
    const user = userEvent.setup();
    const onSaveChannel = vi.fn().mockRejectedValue(new Error("save failed"));
    renderSettings({ onSaveChannel });

    await user.click(screen.getByRole("checkbox", { name: "텔레그램 켜기" }));
    await user.type(screen.getByLabelText("Bot token"), "retry-secret");
    await user.type(screen.getByLabelText("Chat ID"), "123");
    await user.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "저장" }).hasAttribute("disabled"))
      .toBe(false));
    expect((screen.getByLabelText("Bot token") as HTMLInputElement).value).toBe("retry-secret");
    await user.click(screen.getByRole("button", { name: "취소" }));
    await user.click(screen.getByRole("checkbox", { name: "텔레그램 켜기" }));
    expect((screen.getByLabelText("Bot token") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Chat ID") as HTMLInputElement).value).toBe("");
  });

  it("trims submitted channel fields without exposing secret values in validation", async () => {
    const user = userEvent.setup();
    const onSaveChannel = vi.fn().mockResolvedValue(undefined);
    renderSettings({ onSaveChannel });

    await user.click(screen.getByRole("checkbox", { name: "텔레그램 켜기" }));
    await user.type(screen.getByLabelText("표시 이름"), "  업무 알림  ");
    await user.type(screen.getByLabelText("Bot token"), "  secret-token  ");
    await user.type(screen.getByLabelText("Chat ID"), "  12345  ");
    await user.click(screen.getByRole("button", { name: "저장" }));

    expect(onSaveChannel).toHaveBeenCalledWith({
      kind: "telegram",
      name: "업무 알림",
      config: { bot_token: "secret-token", chat_id: "12345" },
    });
  });

  it("blocks incomplete Telegram settings with a secret-free inline error", async () => {
    const user = userEvent.setup();
    const onSaveChannel = vi.fn().mockResolvedValue(undefined);
    renderSettings({ onSaveChannel });

    await user.click(screen.getByRole("checkbox", { name: "텔레그램 켜기" }));
    await user.click(screen.getByRole("button", { name: "저장" }));

    expect(onSaveChannel).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toBe("Telegram Bot token을 입력해 주세요.");
    expect(screen.getByRole("alert").textContent).not.toContain("token=");
  });

  it.each([
    ["", "Webhook HTTPS URL을 입력해 주세요."],
    ["http://hooks.example/rail", "Webhook URL은 HTTPS 주소로 입력해 주세요."],
    ["not-a-url", "올바른 Webhook HTTPS URL을 입력해 주세요."],
  ])("blocks an unsafe Webhook URL: %s", async (url, expectedError) => {
    const user = userEvent.setup();
    const onSaveChannel = vi.fn().mockResolvedValue(undefined);
    renderSettings({ onSaveChannel });

    await user.click(screen.getByRole("checkbox", { name: "디스코드 켜기" }));
    if (url) await user.type(screen.getByLabelText("HTTPS URL"), url);
    await user.click(screen.getByRole("button", { name: "저장" }));

    expect(onSaveChannel).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toBe(expectedError);
  });

  it("toggles and tests an existing channel with accessible controls", async () => {
    const user = userEvent.setup();
    const existing = channel();
    const onToggleChannel = vi.fn().mockResolvedValue(undefined);
    const onTestChannel = vi.fn().mockResolvedValue(undefined);
    renderSettings({ channels: [existing], onToggleChannel, onTestChannel });

    await user.click(screen.getByRole("checkbox", { name: "텔레그램 끄기" }));
    expect(onToggleChannel).toHaveBeenCalledWith(existing, false);
    await user.click(screen.getByRole("button", { name: "텔레그램 시험 알림 보내기" }));
    expect(onTestChannel).toHaveBeenCalledWith(existing);
  });

  it("treats an unconfigured channel record as setup-required", async () => {
    const user = userEvent.setup();
    const onToggleChannel = vi.fn().mockResolvedValue(undefined);
    renderSettings({
      channels: [channel({ configured: false, enabled: false })],
      onToggleChannel,
    });

    expect(screen.queryByRole("button", { name: "텔레그램 시험 알림 보내기" })).toBeNull();
    await user.click(screen.getByRole("checkbox", { name: "텔레그램 켜기" }));

    expect(onToggleChannel).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: "텔레그램 연결" })).toBeTruthy();
  });

  it("does not offer a test send for a disabled channel", () => {
    renderSettings({ channels: [channel({ enabled: false })] });

    expect((screen.getByRole("button", {
      name: "텔레그램 시험 알림 보내기",
    }) as HTMLButtonElement).disabled).toBe(true);
  });

  it.each([
    {
      name: "checking leaves this device unselected until its key is known",
      state: browserPushState({ support: "checking" }),
      checked: false,
      switchDisabled: false,
      hasTest: false,
      detail: "이 기기 구독 확인 중… · 전체 활성 기기 1대",
    },
    {
      name: "unsupported disables setup",
      state: browserPushState({ support: "unsupported" }),
      checked: false,
      switchDisabled: true,
      hasTest: false,
      detail: "이 브라우저는 OS 알림을 지원하지 않음 · 전체 활성 기기 1대",
    },
    {
      name: "insecure disables setup",
      state: browserPushState({ support: "insecure" }),
      checked: false,
      switchDisabled: true,
      hasTest: false,
      detail: "HTTPS 또는 localhost 접속 필요 · 전체 활성 기기 1대",
    },
    {
      name: "denied is fail-closed",
      state: browserPushState({ permission: "denied" }),
      checked: false,
      switchDisabled: false,
      hasTest: false,
      detail: "브라우저 사이트 설정에서 알림 권한이 차단됨 · 전체 활성 기기 1대",
    },
    {
      name: "subscribed is ready",
      state: browserPushState({
        permission: "granted",
        subscribed: true,
        deviceKey: "device-one",
      }),
      checked: true,
      switchDisabled: false,
      hasTest: true,
      detail: "이 기기 사용 중 · 전체 활성 기기 1대",
    },
  ])("renders Web Push state: $name", ({
    state,
    checked,
    switchDisabled,
    hasTest,
    detail,
  }) => {
    renderSettings({
      channels: [channel({
        id: "push-1",
        kind: "web_push",
        name: "내 PC",
        deviceKey: "device-one",
        activeDeviceCount: 1,
      })],
      browserPushState: state,
    });

    const toggle = screen.getByRole("checkbox", { name: `OS 알림 ${checked ? "끄기" : "켜기"}` });
    expect((toggle as HTMLInputElement).checked).toBe(checked);
    expect((toggle as HTMLInputElement).disabled).toBe(switchDisabled);
    expect(Boolean(screen.queryByRole("button", {
      name: "OS 알림 시험 알림 보내기",
    }))).toBe(hasTest);
    expect(screen.getByText(detail)).toBeTruthy();
    expect(within(toggle.closest(".setting-row") as HTMLElement)
      .getByText(/기기·브라우저마다 한 번씩 연결/)).toBeTruthy();
  });

  it("shows other active devices without treating them as this device", () => {
    renderSettings({
      channels: [channel({
        id: "push-other",
        kind: "web_push",
        name: "다른 기기",
        deviceKey: "device-two",
        activeDeviceCount: 2,
      })],
      browserPushState: browserPushState({
        permission: "granted",
        subscribed: true,
        deviceKey: "device-one",
      }),
    });

    expect(screen.getByText("이 기기는 서버에 연결되지 않음 · 전체 활성 기기 2대"))
      .toBeTruthy();
    expect(screen.getByRole("checkbox", { name: "OS 알림 켜기" })).toBeTruthy();
  });
});
