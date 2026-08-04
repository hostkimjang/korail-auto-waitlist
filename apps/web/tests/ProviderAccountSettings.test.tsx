import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ProviderAccountSettings } from "../src/features/settings/ProviderAccountSettings";

describe("rail provider account settings", () => {
  it("saves a new account without retaining the password in the form", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<ProviderAccountSettings accounts={[]} onSave={onSave} onDelete={vi.fn()} />);

    const korailCard = screen.getByRole("heading", { name: "KORAIL 계정" }).closest("section");
    if (!(korailCard instanceof HTMLElement)) throw new Error("KORAIL 계정 카드를 찾을 수 없습니다.");
    const card = within(korailCard);
    const loginField = card.getByRole("textbox", { name: "회원번호" });
    const passwordField = card.getByLabelText("비밀번호");
    const saveButton = card.getByRole("button", { name: "로그인 확인 후 저장" });
    if (!(loginField instanceof HTMLInputElement)
      || !(passwordField instanceof HTMLInputElement)
      || !(saveButton instanceof HTMLButtonElement)) {
      throw new Error("KORAIL 계정 입력 폼을 찾을 수 없습니다.");
    }
    await user.type(loginField, "rail-user");
    await user.type(passwordField, "temporary-password");
    await user.click(saveButton);

    await waitFor(() => expect(onSave).toHaveBeenCalledWith("KORAIL", {
      loginMethod: "membership_number",
      loginId: "rail-user",
      password: "temporary-password",
      enabled: true,
    }));
    expect(passwordField.value).toBe("");
  });

  it("uses method-specific fields and keeps the identifier when login verification fails", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockRejectedValue(new Error("로그인 정보를 확인해 주세요."));
    render(<ProviderAccountSettings accounts={[]} onSave={onSave} onDelete={vi.fn()} />);

    const korailCard = screen.getByRole("heading", { name: "KORAIL 계정" }).closest("section");
    if (!(korailCard instanceof HTMLElement)) throw new Error("KORAIL 계정 카드를 찾을 수 없습니다.");
    const card = within(korailCard);
    await user.click(card.getByRole("radio", { name: "이메일" }));
    const loginField = card.getByLabelText("이메일 주소");
    const passwordField = card.getByLabelText("비밀번호");
    if (!(loginField instanceof HTMLInputElement) || !(passwordField instanceof HTMLInputElement)) {
      throw new Error("이메일 로그인 입력 폼을 찾을 수 없습니다.");
    }
    expect(loginField.type).toBe("email");
    expect(loginField.autocomplete).toBe("off");
    expect(loginField.dataset.lpignore).toBe("true");
    expect(loginField.dataset["1pIgnore"]).toBe("true");
    expect(passwordField.autocomplete).toBe("new-password");
    expect(passwordField.dataset.lpignore).toBe("true");
    expect(passwordField.dataset["1pIgnore"]).toBe("true");

    await user.type(loginField, "rail@example.com");
    await user.type(passwordField, "wrong-password");
    await user.click(card.getByRole("button", { name: "로그인 확인 후 저장" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledWith("KORAIL", {
      loginMethod: "email",
      loginId: "rail@example.com",
      password: "wrong-password",
      enabled: true,
    }));
    expect((await card.findByRole("alert")).textContent).toContain("로그인 정보를 확인해 주세요.");
    expect(loginField.value).toBe("rail@example.com");
    expect(passwordField.value).toBe("");
  });

  it("shows only the masked login id for a configured account and can disconnect it", async () => {
    const user = userEvent.setup();
    const onDelete = vi.fn().mockResolvedValue(undefined);
    render(<ProviderAccountSettings accounts={[{
      provider: "KORAIL",
      configured: true,
      enabled: true,
      loginMethod: "phone",
      maskedLoginId: "ra***er",
      credentialVersion: 1,
      lastAuthStatus: "authenticated",
      lastAuthenticatedAt: "2026-08-01T10:00:00+09:00",
      updatedAt: "2026-08-01T10:00:00+09:00",
    }]} onSave={vi.fn()} onDelete={onDelete} />);

    const korailCard = screen.getByRole("heading", { name: "KORAIL 계정" }).closest("section");
    if (!(korailCard instanceof HTMLElement)) throw new Error("KORAIL 계정 카드를 찾을 수 없습니다.");
    expect(within(korailCard).getByText("ra***er")).toBeTruthy();
    expect(within(korailCard).getByText("휴대전화")).toBeTruthy();
    expect(screen.queryByDisplayValue("temporary-password")).toBeNull();
    await user.click(screen.getByRole("button", { name: "연결 해제" }));
    expect(onDelete).toHaveBeenCalledWith("KORAIL");
  });
});
