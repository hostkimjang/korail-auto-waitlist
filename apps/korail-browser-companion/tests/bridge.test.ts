import { afterEach, describe, expect, it, vi } from "vitest";

import { normalizeBridgeSettings, pairBridge, postSnapshot } from "../src/bridge";

const credentialId = "11111111-1111-4111-8111-111111111111";
const clientId = "22222222-2222-4222-8222-222222222222";
const settings = { bridgeToken: "a".repeat(48), credentialId, clientId };

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("normalizeBridgeSettings", () => {
  it("accepts loopback HTTP and remote HTTPS origins", () => {
    expect(normalizeBridgeSettings({
      serviceBaseUrl: "http://127.0.0.1:4173/",
      ...settings,
    })).toEqual({ serviceBaseUrl: "http://127.0.0.1:4173", ...settings });
    expect(normalizeBridgeSettings({
      serviceBaseUrl: "https://rail.example.test/",
      ...settings,
    })).toEqual({ serviceBaseUrl: "https://rail.example.test", ...settings });
  });

  it("rejects remote plain HTTP, URL credentials and short tokens", () => {
    expect(normalizeBridgeSettings({
      serviceBaseUrl: "http://rail.example.test",
      ...settings,
    })).toBeNull();
    expect(normalizeBridgeSettings({
      serviceBaseUrl: "https://user:password@rail.example.test",
      ...settings,
    })).toBeNull();
    expect(normalizeBridgeSettings({
      serviceBaseUrl: "https://rail.example.test",
      bridgeToken: "short",
      credentialId,
      clientId,
    })).toBeNull();
  });

  it("exchanges a one-time code and stores only the installation credential", async () => {
    const storageSet = vi.fn(async () => undefined);
    vi.stubGlobal("chrome", {
      permissions: { request: vi.fn(async () => true) },
      storage: { local: { set: storageSet } },
    });
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      credential_id: credentialId,
      bridge_token: settings.bridgeToken,
      label: "내 브라우저",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await pairBridge("http://127.0.0.1:4173", "p".repeat(43));

    expect(result.ok).toBe(true);
    expect(storageSet).toHaveBeenCalledOnce();
    expect(JSON.stringify((storageSet.mock.calls as unknown[][])[0])).not.toContain("p".repeat(43));
    expect((fetchMock.mock.calls as unknown[][])[0]?.[0]).toBe("http://127.0.0.1:4173/api/v1/browser-bridge/pair");
  });

  it("binds each snapshot body to a fresh server challenge", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ challenge: "single-use-challenge" }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ accepted_trains: 1 }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }));
    vi.stubGlobal("fetch", fetchMock);
    const payload = {
      origin: "대전",
      destination: "서울",
      travel_date: "2030-07-30",
      passenger_count: 1 as const,
      trains: [{
        train_number: "26",
        departure_at: "2030-07-30T12:00:00+09:00",
        standard: "sold_out" as const,
        first: "available" as const,
      }],
    };

    const result = await postSnapshot({
      serviceBaseUrl: "http://127.0.0.1:4173",
      ...settings,
    }, payload);

    expect(result).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const challengeRequest = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(challengeRequest.body)).body_sha256).toMatch(/^[0-9a-f]{64}$/);
    const snapshotRequest = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(new Headers(snapshotRequest.headers).get("X-Rail-Bridge-Challenge")).toBe("single-use-challenge");
    expect(snapshotRequest.body).toBe(JSON.stringify(payload));
  });
});
