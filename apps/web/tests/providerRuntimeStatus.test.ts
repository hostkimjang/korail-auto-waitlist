import { describe, expect, it } from "vitest";

import type { ProviderRuntimeStatus } from "../src/api/providerRuntime";
import {
  formatRuntimeLocalReuseWindow,
  formatRuntimeVerifiedAge,
  providerRuntimeStatusPresentation,
} from "../src/features/settings/providerRuntimeStatus";

const readyRuntime: ProviderRuntimeStatus = {
  provider: "KORAIL",
  state: "ready",
  credentialGeneration: "1",
  createdAgeSeconds: 120,
  lastVerifiedAgeSeconds: 90,
  lastUsedAgeSeconds: 30,
  localReuseRemainingSeconds: 130,
  locallyReusable: true,
  prewarmOutcome: "authenticated",
};

describe("provider runtime status presentation", () => {
  it("uses explicit, readable labels for every runtime state", () => {
    expect(providerRuntimeStatusPresentation(readyRuntime)).toMatchObject({
      label: "재사용 가능",
      tone: "ready",
    });
    expect(providerRuntimeStatusPresentation({ ...readyRuntime, state: "authenticating" }).label)
      .toBe("로그인 준비 중");
    expect(providerRuntimeStatusPresentation({ ...readyRuntime, state: "stale" }).label)
      .toBe("재검증 예정");
    expect(providerRuntimeStatusPresentation({ ...readyRuntime, state: "auth_required" }).label)
      .toBe("로그인 필요");
    expect(providerRuntimeStatusPresentation({ ...readyRuntime, state: "blocked" }).label)
      .toBe("운영사 제한");
    expect(providerRuntimeStatusPresentation({ ...readyRuntime, state: "cold" }).label)
      .toBe("시작 전");
    expect(providerRuntimeStatusPresentation({ ...readyRuntime, locallyReusable: false }).label)
      .toBe("재검증 예정");
  });

  it("rounds verification age and the local reuse window for people", () => {
    expect(formatRuntimeVerifiedAge(20)).toBe("방금");
    expect(formatRuntimeVerifiedAge(90)).toBe("2분 전");
    expect(formatRuntimeVerifiedAge(null)).toBe("확인 기록 없음");
    expect(formatRuntimeLocalReuseWindow(130, true)).toBe("약 2분 남음");
    expect(formatRuntimeLocalReuseWindow(0, true)).toBe("종료됨");
    expect(formatRuntimeLocalReuseWindow(240, false)).toBe("사용 불가");
  });
});
