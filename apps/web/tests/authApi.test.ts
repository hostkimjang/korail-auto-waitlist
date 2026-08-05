import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  getAuthStatus,
  loginWithPassword,
  logout,
  registerAdmin,
} from "../src/api/auth";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("administrator authentication API boundary", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    Object.defineProperty(document, "cookie", {
      configurable: true,
      writable: true,
      value: "rail_csrf=csrf-token",
    });
  });

  it("reads the administrator status with the shared cookie request contract", async () => {
    const payload = {
      configured: true,
      authenticated: false,
      registration_allowed: false,
    };
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(payload));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getAuthStatus()).resolves.toEqual(payload);

    const requestCall = fetchMock.mock.calls[0];
    expect(requestCall).toBeDefined();
    const [url, options] = requestCall ?? [];
    expect(url).toBe("/api/v1/auth/status");
    expect(options).toMatchObject({ method: "GET", credentials: "include" });
    const headers = new Headers(options?.headers);
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.has("X-CSRF-Token")).toBe(false);
  });

  it.each([
    ["register", registerAdmin, "/api/v1/auth/register"],
    ["login", loginWithPassword, "/api/v1/auth/login"],
  ] as const)("posts %s credentials in the snake_case auth contract without CSRF", async (
    _operation,
    action,
    expectedUrl,
  ) => {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValue(jsonResponse({ authenticated: true }));
    vi.stubGlobal("fetch", fetchMock);

    await action("admin.user", "correct horse battery staple");

    const requestCall = fetchMock.mock.calls[0];
    expect(requestCall).toBeDefined();
    const [url, options] = requestCall ?? [];
    expect(url).toBe(expectedUrl);
    expect(options).toMatchObject({ method: "POST", credentials: "include" });
    expect(JSON.parse(String(options?.body))).toEqual({
      username: "admin.user",
      password: "correct horse battery staple",
    });
    const headers = new Headers(options?.headers);
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.has("X-CSRF-Token")).toBe(false);
  });

  it("protects logout with the shared CSRF cookie and credential contract", async () => {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(logout()).resolves.toBeNull();

    const requestCall = fetchMock.mock.calls[0];
    expect(requestCall).toBeDefined();
    const [url, options] = requestCall ?? [];
    expect(url).toBe("/api/v1/auth/logout");
    expect(options).toMatchObject({ method: "POST", credentials: "include" });
    const headers = new Headers(options?.headers);
    expect(headers.get("X-CSRF-Token")).toBe("csrf-token");
  });

  it("preserves the shared structured ApiError contract", async () => {
    const detail = {
      code: "invalid_credentials",
      reason: "authentication_failed",
      message: "관리자 ID 또는 비밀번호를 확인해 주세요.",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ detail }, 401)),
    );

    const request = loginWithPassword("admin", "incorrect password");

    await expect(request).rejects.toMatchObject({
      name: "ApiError",
      status: 401,
      detail: { detail },
      code: "invalid_credentials",
      reason: "authentication_failed",
      message: detail.message,
    });
    await expect(request).rejects.toBeInstanceOf(ApiError);
  });

  it("uses the dedicated administrator registration endpoint without a bootstrap header", async () => {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValue(jsonResponse({ authenticated: true }, 201));
    vi.stubGlobal("fetch", fetchMock);

    await registerAdmin("admin", "x".repeat(16));
    const requestCall = fetchMock.mock.calls[0];
    expect(requestCall).toBeDefined();
    const [url, options] = requestCall ?? [];
    expect(String(url)).toContain("/auth/register");
    const headers = new Headers(options?.headers);
    expect(headers.has("X-Bootstrap-Token")).toBe(false);
    expect(JSON.parse(String(options?.body))).toEqual({
      username: "admin",
      password: expect.any(String),
    });
  });

  it("uses the administrator password login endpoint", async () => {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValue(jsonResponse({ authenticated: true }));
    vi.stubGlobal("fetch", fetchMock);

    await loginWithPassword("admin", "x".repeat(16));
    const requestCall = fetchMock.mock.calls[0];
    expect(requestCall).toBeDefined();
    const [url, options] = requestCall ?? [];
    expect(String(url)).toContain("/auth/login");
    expect(JSON.parse(String(options?.body))).toEqual({
      username: "admin",
      password: expect.any(String),
    });
  });
});
