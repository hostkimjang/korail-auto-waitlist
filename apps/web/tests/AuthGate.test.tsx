import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const authApi = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;

    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  }

  return {
    ApiError,
    loginWithPassword: vi.fn(),
    registerAdmin: vi.fn(),
  };
});

vi.mock("../src/api/auth", () => authApi);

import { AuthGate } from "../src/features/auth/AuthGate";
import type { AuthState } from "../src/features/auth/useAuthState";

const unconfigured: AuthState = {
  loading: false,
  configured: false,
  authenticated: false,
  registrationAllowed: true,
  demo: false,
  error: null,
};

const configured: AuthState = {
  ...unconfigured,
  configured: true,
  registrationAllowed: false,
};

afterEach(() => {
  vi.clearAllMocks();
});

describe("AuthGate administrator credentials flow", () => {
  it("creates the first administrator account and enters the app", async () => {
    const user = userEvent.setup();
    const onAuthenticated = vi.fn();
    authApi.registerAdmin.mockResolvedValue({ authenticated: true });
    render(<AuthGate status={unconfigured} onAuthenticated={onAuthenticated} onRetryStatus={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "초기 관리자 등록" })).toBeTruthy();
    expect(screen.queryByText(/Passkey|bootstrap|복구 코드/i)).toBeNull();

    await user.type(screen.getByLabelText("관리자 ID"), "admin");
    await user.type(screen.getByLabelText("비밀번호"), "x".repeat(16));
    await user.type(screen.getByLabelText("비밀번호 확인"), "x".repeat(16));
    await user.click(screen.getByRole("button", { name: "관리자 계정 만들기" }));

    await waitFor(() => expect(authApi.registerAdmin).toHaveBeenCalledWith("admin", expect.any(String)));
    expect(onAuthenticated).toHaveBeenCalledOnce();
  });

  it("does not submit mismatching registration passwords", async () => {
    const user = userEvent.setup();
    render(<AuthGate status={unconfigured} onAuthenticated={vi.fn()} onRetryStatus={vi.fn()} />);

    await user.type(screen.getByLabelText("관리자 ID"), "admin");
    await user.type(screen.getByLabelText("비밀번호"), "x".repeat(16));
    await user.type(screen.getByLabelText("비밀번호 확인"), "y".repeat(16));
    await user.click(screen.getByRole("button", { name: "관리자 계정 만들기" }));

    expect(screen.getByRole("alert").textContent).toContain("일치하지 않습니다");
    expect(authApi.registerAdmin).not.toHaveBeenCalled();
  });

  it("refreshes auth status when another client wins first registration", async () => {
    const user = userEvent.setup();
    const onRetryStatus = vi.fn().mockResolvedValue(undefined);
    authApi.registerAdmin.mockRejectedValue(new authApi.ApiError("already configured", 409));
    render(<AuthGate status={unconfigured} onAuthenticated={vi.fn()} onRetryStatus={onRetryStatus} />);

    await user.type(screen.getByLabelText("관리자 ID"), "admin");
    await user.type(screen.getByLabelText("비밀번호"), "x".repeat(16));
    await user.type(screen.getByLabelText("비밀번호 확인"), "x".repeat(16));
    await user.click(screen.getByRole("button", { name: "관리자 계정 만들기" }));

    await waitFor(() => expect(onRetryStatus).toHaveBeenCalledOnce());
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows only ID and password after setup and logs in", async () => {
    const user = userEvent.setup();
    const onAuthenticated = vi.fn();
    authApi.loginWithPassword.mockResolvedValue({ authenticated: true });
    render(<AuthGate status={configured} onAuthenticated={onAuthenticated} onRetryStatus={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "관리자 로그인" })).toBeTruthy();
    expect(screen.queryByLabelText("비밀번호 확인")).toBeNull();
    expect(screen.queryByRole("button", { name: "관리자 계정 만들기" })).toBeNull();

    await user.type(screen.getByLabelText("관리자 ID"), "admin");
    await user.type(screen.getByLabelText("비밀번호"), "x".repeat(16));
    await user.click(screen.getByRole("button", { name: "로그인" }));

    await waitFor(() => expect(authApi.loginWithPassword).toHaveBeenCalledWith("admin", expect.any(String)));
    expect(onAuthenticated).toHaveBeenCalledOnce();
  });

  it("fails closed when status lookup failed and allows retry", async () => {
    const user = userEvent.setup();
    const onRetryStatus = vi.fn();
    render(<AuthGate status={{ ...unconfigured, registrationAllowed: false, error: "연결 실패" }} onAuthenticated={vi.fn()} onRetryStatus={onRetryStatus} />);

    expect(screen.getByRole("heading", { name: "서버 연결을 확인해 주세요" })).toBeTruthy();
    expect(screen.queryByLabelText("관리자 ID")).toBeNull();
    await user.click(screen.getByRole("button", { name: "다시 확인" }));
    expect(onRetryStatus).toHaveBeenCalledOnce();
  });
});
