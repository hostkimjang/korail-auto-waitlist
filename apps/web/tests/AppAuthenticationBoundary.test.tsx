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

import { AppAuthenticationBoundary } from "../src/app/AppAuthenticationBoundary";
import type { AuthState } from "../src/features/auth/useAuthState";

const authenticatedStatus: AuthState = {
  loading: false,
  configured: true,
  authenticated: true,
  registrationAllowed: false,
  demo: false,
  error: null,
};

const unauthenticatedStatus: AuthState = {
  ...authenticatedStatus,
  authenticated: false,
};

afterEach(() => {
  vi.clearAllMocks();
});

describe("AppAuthenticationBoundary", () => {
  it("renders only the exact loading surface while authentication is loading", () => {
    const onAuthenticated = vi.fn();
    const onRetryStatus = vi.fn();
    const { container } = render(
      <AppAuthenticationBoundary
        status={{ ...authenticatedStatus, loading: true }}
        onAuthenticated={onAuthenticated}
        onRetryStatus={onRetryStatus}
      >
        <section data-testid="authenticated-content">인증된 화면</section>
      </AppAuthenticationBoundary>,
    );

    const main = container.firstElementChild;
    expect(main?.tagName).toBe("MAIN");
    expect(main?.className).toBe("auth-page");
    expect(main?.children).toHaveLength(1);
    expect(main?.firstElementChild?.className).toBe("loading-state");

    const image = container.querySelector("img");
    expect(image?.getAttribute("src")).toBe("/icons/app-icon-any-512-v2.png");
    expect(image?.getAttribute("alt")).toBe("");
    expect(screen.getByText("안전하게 연결하는 중…")).toBeTruthy();
    expect(screen.queryByTestId("authenticated-content")).toBeNull();
    expect(onAuthenticated).not.toHaveBeenCalled();
    expect(onRetryStatus).not.toHaveBeenCalled();
  });

  it("renders the unauthenticated login surface and forwards authentication", async () => {
    const user = userEvent.setup();
    const onAuthenticated = vi.fn();
    const onRetryStatus = vi.fn();
    authApi.loginWithPassword.mockResolvedValue({ authenticated: true });
    render(
      <AppAuthenticationBoundary
        status={unauthenticatedStatus}
        onAuthenticated={onAuthenticated}
        onRetryStatus={onRetryStatus}
      >
        <section data-testid="authenticated-content">인증된 화면</section>
      </AppAuthenticationBoundary>,
    );

    expect(screen.getByRole("heading", { name: "관리자 로그인" })).toBeTruthy();
    expect(screen.queryByTestId("authenticated-content")).toBeNull();

    await user.type(screen.getByLabelText("관리자 ID"), "admin");
    await user.type(screen.getByLabelText("비밀번호"), "valid-password");
    await user.click(screen.getByRole("button", { name: "로그인" }));

    await waitFor(() => expect(onAuthenticated).toHaveBeenCalledOnce());
    expect(authApi.loginWithPassword).toHaveBeenCalledWith("admin", "valid-password");
    expect(onRetryStatus).not.toHaveBeenCalled();
  });

  it("forwards retry from the fail-closed unauthenticated status surface", async () => {
    const user = userEvent.setup();
    const onAuthenticated = vi.fn();
    const onRetryStatus = vi.fn(async () => undefined);
    render(
      <AppAuthenticationBoundary
        status={{ ...unauthenticatedStatus, error: "상태 조회 실패" }}
        onAuthenticated={onAuthenticated}
        onRetryStatus={onRetryStatus}
      >
        <section data-testid="authenticated-content">인증된 화면</section>
      </AppAuthenticationBoundary>,
    );

    expect(screen.getByRole("heading", { name: "서버 연결을 확인해 주세요" })).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("상태 조회 실패");
    await user.click(screen.getByRole("button", { name: "다시 확인" }));

    expect(onRetryStatus).toHaveBeenCalledOnce();
    expect(onAuthenticated).not.toHaveBeenCalled();
    expect(screen.queryByTestId("authenticated-content")).toBeNull();
  });

  it("returns the authenticated child unchanged without invoking auth callbacks", () => {
    const onAuthenticated = vi.fn();
    const onRetryStatus = vi.fn();
    const { container } = render(
      <AppAuthenticationBoundary
        status={authenticatedStatus}
        onAuthenticated={onAuthenticated}
        onRetryStatus={onRetryStatus}
      >
        <section className="authenticated-content">인증된 화면</section>
      </AppAuthenticationBoundary>,
    );

    expect(container.firstElementChild?.tagName).toBe("SECTION");
    expect(container.firstElementChild?.className).toBe("authenticated-content");
    expect(screen.getByText("인증된 화면")).toBeTruthy();
    expect(screen.queryByRole("main")).toBeNull();
    expect(onAuthenticated).not.toHaveBeenCalled();
    expect(onRetryStatus).not.toHaveBeenCalled();
  });
});
