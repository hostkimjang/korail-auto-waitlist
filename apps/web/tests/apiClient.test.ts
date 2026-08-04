import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, request } from "../src/api/client";

describe("API client transport contract", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    Object.defineProperty(document, "cookie", {
      configurable: true,
      writable: true,
      value: "rail_csrf=csrf-token",
    });
  });

  it("keeps structured error details on the shared client boundary", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: {
        code: "registration_evidence_conflict",
        reason: "expired",
        message: "좌석 등록 근거가 만료되었습니다.",
      },
    }), {
      status: 409,
      headers: { "Content-Type": "application/json" },
    })));

    const failure = await request("/watches", { method: "POST", body: "{}" })
      .catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({
      status: 409,
      code: "registration_evidence_conflict",
      reason: "expired",
      operation: null,
    });
  });

  it("preserves CSRF opt-out and no-content responses", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(request("/auth/login", {
      method: "POST",
      body: "{}",
      skipCsrf: true,
    })).resolves.toBeNull();

    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(options.headers);
    expect(url).toBe("/api/v1/auth/login");
    expect(options.credentials).toBe("include");
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.has("X-CSRF-Token")).toBe(false);
  });
});
