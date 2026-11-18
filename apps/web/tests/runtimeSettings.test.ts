import { afterEach, describe, expect, it } from "vitest";

import { parseRuntimeSettings, runtimeSettings } from "../src/shared/lib/runtimeSettings";

describe("runtime settings from the web container", () => {
  afterEach(() => {
    delete window.__RAILWAIT_RUNTIME_CONFIG__;
  });

  it("keeps only known string settings up to 64 characters", () => {
    expect(parseRuntimeSettings({
      VITE_KORAIL_BOOKING_DEEPLINK_ENABLED: "true",
      VITE_KORAIL_BOOKING_VALIDATED_VERSION: "7.0.0+70000006",
      VITE_KORAIL_TICKET_DEEPLINK_ENABLED: true,
      VITE_SRT_MAIN_VALIDATED_VERSION: "x".repeat(65),
      VITE_SRT_TICKET_VALIDATED_VERSION: 2,
      VITE_UNKNOWN_SETTING: "true",
    })).toEqual({
      VITE_KORAIL_BOOKING_DEEPLINK_ENABLED: "true",
      VITE_KORAIL_BOOKING_VALIDATED_VERSION: "7.0.0+70000006",
    });
  });

  it("ignores a missing or malformed settings object", () => {
    for (const source of [undefined, null, "true", ["true"], 1]) {
      expect(parseRuntimeSettings(source)).toEqual({});
    }
  });

  it("reads the object the runtime-config script leaves on window", () => {
    expect(runtimeSettings()).toEqual({});
    window.__RAILWAIT_RUNTIME_CONFIG__ = { VITE_SRT_MAIN_DEEPLINK_ENABLED: "false" };
    expect(runtimeSettings()).toEqual({ VITE_SRT_MAIN_DEEPLINK_ENABLED: "false" });
  });
});
