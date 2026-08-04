import { describe, expect, it } from "vitest";

import { createReservationPolicyMutationGuard } from
  "../src/features/home/reservationPolicyMutationGuard";

describe("reservation policy mutation guard", () => {
  it("rejects GET snapshots that cross either edge of a policy PATCH", () => {
    const guard = createReservationPolicyMutationGuard();
    const beforePatch = guard.snapshot();

    guard.begin();
    expect(guard.isCurrent(beforePatch)).toBe(false);

    const duringPatch = guard.snapshot();
    guard.end();
    expect(guard.isCurrent(duringPatch)).toBe(false);
    expect(guard.isCurrent(guard.snapshot())).toBe(true);
  });
});
