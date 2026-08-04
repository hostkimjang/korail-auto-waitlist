import { afterEach, describe, expect, it, vi } from "vitest";

import {
  deleteProviderAccount,
  fetchProviderAccounts,
  saveProviderAccount,
} from "../src/api/providerAccounts";

const accountDto = {
  provider: "KORAIL",
  configured: true,
  enabled: true,
  login_method: "email",
  masked_login_id: "ra***er",
  credential_version: 2,
  last_auth_status: "authenticated",
  last_authenticated_at: "2026-08-01T10:00:00+09:00",
  updated_at: "2026-08-01T10:01:00+09:00",
};

afterEach(() => {
  vi.unstubAllGlobals();
  document.cookie = "rail_csrf=; Max-Age=0; Path=/";
});

describe("provider accounts API", () => {
  it("normalizes the provider account list without exposing credentials", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify([accountDto]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchProviderAccounts()).resolves.toEqual([{
      provider: "KORAIL",
      configured: true,
      enabled: true,
      loginMethod: "email",
      maskedLoginId: "ra***er",
      credentialVersion: 2,
      lastAuthStatus: "authenticated",
      lastAuthenticatedAt: "2026-08-01T10:00:00+09:00",
      updatedAt: "2026-08-01T10:01:00+09:00",
    }]);
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/provider-accounts", expect.objectContaining({
      credentials: "include",
    }));
  });

  it("accepts a null login method for an unconfigured provider", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify([{
      ...accountDto,
      configured: false,
      enabled: false,
      login_method: null,
      masked_login_id: null,
      credential_version: 0,
      last_auth_status: "not_checked",
      last_authenticated_at: null,
      updated_at: null,
    }]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchProviderAccounts()).resolves.toEqual([expect.objectContaining({
      configured: false,
      loginMethod: null,
      maskedLoginId: null,
    })]);
  });

  it("sends credentials only in the protected request body and supports deletion", async () => {
    document.cookie = "rail_csrf=csrf-test; Path=/";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(accountDto), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await saveProviderAccount("KORAIL", {
      loginMethod: "phone",
      loginId: "rail-user",
      password: "temporary-password",
      enabled: true,
    });
    await deleteProviderAccount("KORAIL");

    const saveCall = fetchMock.mock.calls.at(0);
    const deleteCall = fetchMock.mock.calls.at(1);
    if (!saveCall || !deleteCall) throw new Error("계정 API 호출을 확인할 수 없습니다.");
    const saveInit = saveCall[1];
    const deleteInit = deleteCall[1];
    if (typeof saveInit !== "object" || saveInit === null
      || typeof deleteInit !== "object" || deleteInit === null) {
      throw new Error("계정 API 옵션을 확인할 수 없습니다.");
    }
    expect(saveCall[0]).toBe("/api/v1/provider-accounts/korail");
    expect(saveInit.method).toBe("PUT");
    expect(saveInit.body).toBe(JSON.stringify({
      login_method: "phone",
      login_id: "rail-user",
      password: "temporary-password",
      enabled: true,
    }));
    expect(new Headers(saveInit.headers).get("X-CSRF-Token")).toBe("csrf-test");
    expect(deleteCall[0]).toBe("/api/v1/provider-accounts/korail");
    expect(deleteInit.method).toBe("DELETE");
  });

  it("explains a stale server route instead of exposing the raw Not Found detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: "Not Found",
    }), {
      status: 404,
      headers: { "Content-Type": "application/json" },
    })));

    await expect(fetchProviderAccounts()).rejects.toThrow(
      "철도 계정 API가 현재 서버에 반영되지 않았습니다. 서버를 최신 버전으로 재배포해 주세요.",
    );
  });
});
