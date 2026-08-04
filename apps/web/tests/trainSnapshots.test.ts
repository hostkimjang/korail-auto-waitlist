import { describe, expect, it } from "vitest";

import { jsonBoundaryDeepEqual, reconcileTrainSnapshots } from "../src/features/new-wait/trainSnapshots";

describe("train snapshots", () => {
  it("compares API JSON values independently of object key order", () => {
    expect(jsonBoundaryDeepEqual(
      { id: "KTX-1", seat: { status: "sold_out", fares: [30_000] } },
      { seat: { fares: [30_000], status: "sold_out" }, id: "KTX-1" },
    )).toBe(true);
  });

  it("fails closed for values that cannot cross a JSON API boundary", () => {
    expect(jsonBoundaryDeepEqual({ id: "KTX-1", observedAt: new Date() }, { id: "KTX-1", observedAt: new Date() })).toBe(false);
    expect(jsonBoundaryDeepEqual({ id: "KTX-1", seat: undefined }, { id: "KTX-1", seat: undefined })).toBe(false);
  });

  it("preserves every train and the containing array identity when a refresh is unchanged", () => {
    const previous = [
      { id: "KTX-1", seats: [{ class: "standard", status: "sold_out" }] },
      { id: "SRT-1", seats: [{ class: "first", status: "limited" }] },
    ];
    const incoming = [
      { seats: [{ status: "sold_out", class: "standard" }], id: "KTX-1" },
      { seats: [{ status: "limited", class: "first" }], id: "SRT-1" },
    ];

    const reconciled = reconcileTrainSnapshots(previous, incoming);

    expect(reconciled).toBe(previous);
    expect(reconciled[0]).toBe(previous[0]);
    expect(reconciled[1]).toBe(previous[1]);
  });

  it("reuses only unchanged train cards when one incoming snapshot changes", () => {
    const previous = [
      { id: "KTX-1", seats: [{ class: "standard", status: "sold_out" }] },
      { id: "SRT-1", seats: [{ class: "first", status: "limited" }] },
    ];
    const incoming = [
      { id: "KTX-1", seats: [{ class: "standard", status: "sold_out" }] },
      { id: "SRT-1", seats: [{ class: "first", status: "available" }] },
    ];

    const reconciled = reconcileTrainSnapshots(previous, incoming);

    expect(reconciled).not.toBe(previous);
    expect(reconciled[0]).toBe(previous[0]);
    expect(reconciled[1]).toBe(incoming[1]);
  });

  it("does not reuse ambiguous duplicate train identities", () => {
    const previous = [{ id: "KTX-1", status: "sold_out" }];
    const incoming = [
      { id: "KTX-1", status: "sold_out" },
      { id: "KTX-1", status: "sold_out" },
    ];

    const reconciled = reconcileTrainSnapshots(previous, incoming);

    expect(reconciled[0]).toBe(incoming[0]);
    expect(reconciled[1]).toBe(incoming[1]);
  });
});
