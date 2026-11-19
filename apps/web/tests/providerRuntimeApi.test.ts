import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchProviderRuntimeStatuses,
  mapProviderRuntimeStatuses,
} from "../src/api/providerRuntime";

const runtimeStatusDto = {
  provider: "korail",
  state: "ready",
  credential_generation: "4",
  created_age_seconds: 120,
  last_verified_age_seconds: 90,
  last_used_age_seconds: 10,
  local_reuse_remaining_seconds: 240,
  locally_reusable: true,
  prewarm_outcome: "authenticated",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("provider runtime API boundary", () => {
  it("normalizes the secret-free runtime payload into the UI contract", () => {
    expect(mapProviderRuntimeStatuses([
      runtimeStatusDto,
      {
        ...runtimeStatusDto,
        provider: "srt",
        state: "cold",
        credential_generation: null,
        created_age_seconds: null,
        last_verified_age_seconds: null,
        last_used_age_seconds: null,
        local_reuse_remaining_seconds: null,
        locally_reusable: false,
        prewarm_outcome: null,
        protection_halted: true,
        next_login_attempt_seconds: 1500.5,
      },
    ])).toEqual([
      {
        provider: "KORAIL",
        state: "ready",
        credentialGeneration: "4",
        createdAgeSeconds: 120,
        lastVerifiedAgeSeconds: 90,
        lastUsedAgeSeconds: 10,
        localReuseRemainingSeconds: 240,
        locallyReusable: true,
        prewarmOutcome: "authenticated",
        // APIs before 1.5.0 omit the protection wait fields.
        protectionHalted: false,
        nextLoginAttemptSeconds: null,
      },
      {
        provider: "SRT",
        state: "cold",
        credentialGeneration: null,
        createdAgeSeconds: null,
        lastVerifiedAgeSeconds: null,
        lastUsedAgeSeconds: null,
        localReuseRemainingSeconds: null,
        locallyReusable: false,
        prewarmOutcome: null,
        protectionHalted: true,
        nextLoginAttemptSeconds: 1500.5,
      },
    ]);
  });

  it("rejects malformed protection wait fields", () => {
    for (const malformed of [
      { protection_halted: "true" },
      { protection_halted: null },
      { next_login_attempt_seconds: -1 },
      { next_login_attempt_seconds: "60" },
    ]) {
      expect(() => mapProviderRuntimeStatuses([
        { ...runtimeStatusDto, ...malformed },
        { ...runtimeStatusDto, provider: "srt" },
      ])).toThrow("상주 세션 상태 응답 형식");
    }
  });

  it("rejects malformed and incomplete runtime status lists", () => {
    expect(() => mapProviderRuntimeStatuses([
      { ...runtimeStatusDto, state: "unknown" },
      { ...runtimeStatusDto, provider: "srt" },
    ])).toThrow("상주 세션 상태 응답 형식");
    expect(() => mapProviderRuntimeStatuses([runtimeStatusDto])).toThrow(
      "상주 세션 상태에 KORAIL 또는 SRT 정보가 없습니다.",
    );
  });

  it("requests the current runtime state without caching", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify([
      runtimeStatusDto,
      { ...runtimeStatusDto, provider: "srt" },
    ]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await fetchProviderRuntimeStatuses();

    expect(fetchMock).toHaveBeenCalledWith("/api/v1/provider-runtime-status", {
      headers: { Accept: "application/json" },
      cache: "no-store",
      credentials: "include",
    });
  });
});
