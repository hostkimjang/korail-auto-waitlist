import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ProviderRuntimeStatus } from "../src/api/providerRuntime";
import { ProviderAccountSettings } from "../src/features/settings/ProviderAccountSettings";

describe("rail provider account settings", () => {
  it.each(["KORAIL", "SRT"] as const)(
    "%s separates saved login history from expired, missing and reusable sessions",
    (provider) => {
      const account = {
        provider,
        configured: true,
        enabled: true,
        loginMethod: "membership_number" as const,
        maskedLoginId: "ra***er",
        credentialVersion: 1,
        lastAuthStatus: "authenticated" as const,
        lastAuthenticatedAt: "2026-10-01T10:00:00+09:00",
        updatedAt: "2026-10-01T10:00:00+09:00",
      };
      const runtime: ProviderRuntimeStatus = {
        provider,
        state: "stale",
        credentialGeneration: "1",
        createdAgeSeconds: 600,
        lastVerifiedAgeSeconds: null,
        lastUsedAgeSeconds: 600,
        localReuseRemainingSeconds: null,
        locallyReusable: false,
        prewarmOutcome: "failed",
      };
      const props = { accounts: [account], onSave: vi.fn(), onDelete: vi.fn() };
      const { rerender } = render(
        <ProviderAccountSettings {...props} runtimeStatuses={[runtime]} />,
      );
      const card = within(screen.getByRole("region", { name: `${provider} 계정` }));
      const history = card.getByText("마지막 확인: 로그인 성공");
      expect(history.classList.contains("is-ready")).toBe(false);
      expect(card.queryByText("로그인 확인됨")).toBeNull();
      expect(card.getByText("재검증 예정")).toBeTruthy();
      expect(card.getByText(/서버 로컬 재사용 창: 사용 불가/)).toBeTruthy();

      rerender(<ProviderAccountSettings {...props} runtimeStatuses={[]} />);
      expect(card.getByText("마지막 확인: 로그인 성공")).toBeTruthy();
      expect(card.queryByText("재사용 가능")).toBeNull();

      rerender(<ProviderAccountSettings {...props} runtimeStatuses={[{
        ...runtime,
        state: "ready",
        locallyReusable: true,
        lastVerifiedAgeSeconds: 5,
        localReuseRemainingSeconds: 120,
        prewarmOutcome: "authenticated",
      }]} />);
      expect(card.getByText("마지막 확인: 로그인 성공")).toBeTruthy();
      expect(card.getByText("재사용 가능")).toBeTruthy();

      rerender(<ProviderAccountSettings {...props} runtimeStatuses={[{
        ...runtime,
        state: "ready",
        credentialGeneration: "2",
        locallyReusable: true,
        lastVerifiedAgeSeconds: 5,
        localReuseRemainingSeconds: 120,
        prewarmOutcome: "authenticated",
      }]} />);
      expect(card.getByText("계정 변경 후 재확인")).toBeTruthy();
      expect(card.queryByText("재사용 가능")).toBeNull();
      expect(card.getByText(/서버 로컬 재사용 창: 사용 불가/)).toBeTruthy();

      rerender(<ProviderAccountSettings
        {...props}
        runtimeStatuses={[{
          ...runtime,
          state: "ready",
          locallyReusable: true,
        }]}
        runtimeStatusLoadState="unavailable"
      />);
      expect(card.getByText("상태 확인 실패")).toBeTruthy();
      expect(card.queryByText("재사용 가능")).toBeNull();
      expect(card.getByText(/다시 조회될 때까지 재사용 가능으로 판단하지 않습니다/))
        .toBeTruthy();
    },
  );

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
