import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { UiPreferences } from "../src/api/uiPreferences";
import { Settings } from "../src/App";
import {
  SettingsPage,
  type SettingsPageProps,
} from "../src/features/settings/SettingsPage";

const preferences: UiPreferences = {
  seatObservationIntervalSeconds: 5,
  updatedAt: "2026-08-05T00:00:00Z",
};

function settingsPageProps(
  overrides: Partial<SettingsPageProps> = {},
): SettingsPageProps {
  return {
    channels: [],
    demo: false,
    uiPreferences: preferences,
    savingUiPreferences: false,
    onSaveUiPreferences: vi.fn(async (input) => ({
      ...input,
      updatedAt: preferences.updatedAt,
    })),
    onSaveChannel: vi.fn(async () => undefined),
    onToggleChannel: vi.fn(async () => undefined),
    onTestChannel: vi.fn(async () => undefined),
    onConnectWebPush: vi.fn(async () => undefined),
    onLogout: vi.fn(),
    ...overrides,
  };
}

describe("settings page", () => {
  it("keeps the App compatibility export wired to the strict settings page", () => {
    expect(Settings).toBe(SettingsPage);
  });

  it("opens on notification settings with the accessible settings navigation", () => {
    render(<SettingsPage {...settingsPageProps()} />);

    expect(screen.getByRole("heading", { name: "설정", level: 1 })).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "설정 메뉴" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "알림 채널", level: 2 })).toBeTruthy();
    expect(screen.getByRole("button", { name: /알림 채널/ }).className).toContain("is-active");
  });

  it("reports user section changes and treats initialSection as mount-only", async () => {
    const user = userEvent.setup();
    const onSectionChange = vi.fn();
    const props = settingsPageProps({ onSectionChange, initialSection: "notifications" });
    const view = render(<SettingsPage {...props} />);

    expect(onSectionChange).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: /화면 동작/ }));

    expect(onSectionChange).toHaveBeenCalledOnce();
    expect(onSectionChange).toHaveBeenCalledWith("display");
    expect(screen.getByRole("heading", { name: "화면 동작", level: 2 })).toBeTruthy();
    view.rerender(<SettingsPage {...props} initialSection="security" />);
    expect(onSectionChange).toHaveBeenCalledOnce();
    expect(screen.getByRole("heading", { name: "화면 동작", level: 2 })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "보안", level: 2 })).toBeNull();
  });

  it("keeps logout as an explicit action in the security section", async () => {
    const user = userEvent.setup();
    const onLogout = vi.fn();
    render(
      <SettingsPage
        {...settingsPageProps({ initialSection: "security", onLogout })}
      />,
    );

    await user.click(screen.getByRole("button", { name: "이 기기에서 로그아웃" }));

    expect(onLogout).toHaveBeenCalledOnce();
    expect(screen.getByText("비밀번호는 Argon2id 단방향 해시로 저장됩니다.")).toBeTruthy();
  });

  it("places server settings before the log section and keeps the demo free of server calls", async () => {
    const user = userEvent.setup();
    const onSectionChange = vi.fn();
    const fetcher = vi.spyOn(globalThis, "fetch");
    render(<SettingsPage {...settingsPageProps({ demo: true, onSectionChange })} />);

    const labels = within(screen.getByRole("navigation", { name: "설정 메뉴" }))
      .getAllByRole("button")
      .map((button) => button.textContent);
    expect(labels.indexOf("서버 설정")).toBeGreaterThan(-1);
    expect(labels.indexOf("서버 설정")).toBe(labels.indexOf("로그·진행 상태") - 1);

    await user.click(screen.getByRole("button", { name: /서버 설정/ }));
    expect(onSectionChange).toHaveBeenCalledWith("server");
    expect(screen.getByRole("heading", { name: "서버 설정", level: 2 })).toBeTruthy();
    expect(screen.getByText("설치한 서버의 .env 설정을 바꾸고 필요한 서비스만 다시 시작합니다.")).toBeTruthy();
    expect(screen.getByText("체험 화면에서는 서버 설정을 바꾸지 않습니다.")).toBeTruthy();
    expect(fetcher).not.toHaveBeenCalled();
    fetcher.mockRestore();
  });

  it("passes a secret-bearing editor submission to the App callback without hydrating it", async () => {
    const user = userEvent.setup();
    const onSaveChannel = vi.fn(async () => undefined);
    render(<SettingsPage {...settingsPageProps({ onSaveChannel })} />);

    await user.click(screen.getByRole("checkbox", { name: "텔레그램 켜기" }));
    expect((screen.getByLabelText("Bot token") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Chat ID") as HTMLInputElement).value).toBe("");

    await user.type(screen.getByLabelText("Bot token"), "token");
    await user.type(screen.getByLabelText("Chat ID"), "123");
    await user.click(screen.getByRole("button", { name: "저장" }));

    expect(onSaveChannel).toHaveBeenCalledWith({
      kind: "telegram",
      name: "텔레그램",
      config: { bot_token: "token", chat_id: "123" },
    });
  });
});
