import { describe, expect, it } from "vitest";

import { resolveDemoMode } from "../src/shared/lib/runtimeConfig";

describe("runtime configuration", () => {
  it("allows demo data only in development and honors the explicit opt-out", () => {
    expect(resolveDemoMode(true, undefined)).toBe(true);
    expect(resolveDemoMode(true, "true")).toBe(true);
    expect(resolveDemoMode(true, "false")).toBe(false);
    expect(resolveDemoMode(false, undefined)).toBe(false);
    expect(resolveDemoMode(false, "true")).toBe(false);
  });
});
