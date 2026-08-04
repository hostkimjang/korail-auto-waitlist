import { describe, expect, it } from "vitest";

import { readResponse } from "../src/features/new-wait/korailCompanionBridge";

describe("KORAIL companion page bridge", () => {
  it("accepts only a same-origin response for the matching request", () => {
    const requestId = "11111111-1111-4111-8111-111111111111";
    const event = new MessageEvent("message", {
      source: window,
      origin: window.location.origin,
      data: {
        type: "RAILWAIT_KORAIL_IMPORT_RESPONSE",
        requestId,
        result: {
          ok: true,
          origin: "서울",
          destination: "부산",
          travel_date: "2030-07-30",
          train_count: 12,
        },
      },
    });

    expect(readResponse(event, requestId)).toEqual({
      ok: true,
      origin: "서울",
      destination: "부산",
      travel_date: "2030-07-30",
      train_count: 12,
    });
    expect(readResponse(event, "different-request")).toBeNull();
  });

  it("rejects malformed or cross-origin results", () => {
    const requestId = "11111111-1111-4111-8111-111111111111";
    const malformed = new MessageEvent("message", {
      source: window,
      origin: window.location.origin,
      data: {
        type: "RAILWAIT_KORAIL_IMPORT_RESPONSE",
        requestId,
        result: { ok: true, train_count: -1 },
      },
    });
    const crossOrigin = new MessageEvent("message", {
      source: window,
      origin: "https://other.example",
      data: {
        type: "RAILWAIT_KORAIL_IMPORT_RESPONSE",
        requestId,
        result: { ok: false, code: "bridge_not_paired" },
      },
    });

    expect(readResponse(malformed, requestId)).toBeNull();
    expect(readResponse(crossOrigin, requestId)).toBeNull();
  });
});
