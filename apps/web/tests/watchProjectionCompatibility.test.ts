import { describe, expect, it } from "vitest";

import { mapWatch as mapWatchFromCompatibilityApi } from "../src/api/watches";
import { mapWatch as mapWatchFromProjection } from "../src/api/watchProjection";

describe("watch projection compatibility exports", () => {
  it("keeps the legacy API mapper export as the exact projection function", () => {
    expect(mapWatchFromCompatibilityApi).toBe(mapWatchFromProjection);
  });
});
