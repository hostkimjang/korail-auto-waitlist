import { afterEach, describe, expect, it, vi } from "vitest";

const settings = {
  serviceBaseUrl: "http://127.0.0.1:4173",
  bridgeToken: "a".repeat(48),
  credentialId: "11111111-1111-4111-8111-111111111111",
  clientId: "22222222-2222-4222-8222-222222222222",
};

afterEach(() => {
  vi.resetModules();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("registration-page KORAIL import", () => {
  it("reads one official result tab and posts the exact visible snapshot", async () => {
    const query = vi.fn(async () => [{ id: 9 }]);
    const sendMessage = vi.fn(async () => ({
      ok: true,
      payload: {
        origin: "서울",
        destination: "부산",
        travel_date: "2030-07-30",
        passenger_count: 1,
        trains: [{
          train_number: "61",
          departure_at: "2030-07-30T19:35:00+09:00",
          standard: "available",
          first: "limited",
        }],
      },
    }));
    vi.stubGlobal("chrome", {
      runtime: { onMessage: { addListener: vi.fn() } },
      storage: { local: { get: vi.fn(async () => ({ bridgeSettings: settings })) } },
      tabs: { query, sendMessage },
    });
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ challenge: "one-use" }), { status: 201 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ accepted_trains: 1 }), { status: 201 })));

    const { importCurrentResults } = await import("../src/background");
    const result = await importCurrentResults({ tab: { url: `${settings.serviceBaseUrl}/` } });

    expect(result).toEqual({
      ok: true,
      origin: "서울",
      destination: "부산",
      travel_date: "2030-07-30",
      train_count: 1,
    });
    expect(query).toHaveBeenCalledWith({ url: "https://www.korail.com/ticket/search/list*" });
    expect(sendMessage).toHaveBeenCalledWith(9, { type: "READ_CURRENT_KORAIL_RESULTS" });
  });

  it("rejects a request from a page other than the paired service origin", async () => {
    const query = vi.fn();
    vi.stubGlobal("chrome", {
      runtime: { onMessage: { addListener: vi.fn() } },
      storage: { local: { get: vi.fn(async () => ({ bridgeSettings: settings })) } },
      tabs: { query, sendMessage: vi.fn() },
    });

    const { importCurrentResults } = await import("../src/background");
    const result = await importCurrentResults({ tab: { url: "http://localhost:4173/" } });

    expect(result).toEqual({ ok: false, code: "request_origin_mismatch" });
    expect(query).not.toHaveBeenCalled();
  });
});
