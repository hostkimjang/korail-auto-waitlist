import { describe, expect, it } from "vitest";

import { recoverRefreshedRegistrationTrain } from "../src/features/new-wait/registrationEvidenceRecovery";

type ProvenanceFixture = {
  kind: string;
  source: string;
  observed_at: string;
  fresh_until?: string;
};

type TrainFixture = {
  provider: string;
  train_number: string;
  departure_at: string;
  seat_classes: Array<{
    seat_class: string;
    status: string;
    registration_evidence_id: string;
    provenance: ProvenanceFixture;
    actions: Array<{ kind: string }>;
  }>;
};

function train(status: string, evidenceId: string, overrides: Partial<TrainFixture> = {}): TrainFixture {
  return {
    provider: "SRT",
    train_number: "00330",
    departure_at: "2026-07-31T12:00:00+09:00",
    seat_classes: [{
      seat_class: "standard",
      status,
      registration_evidence_id: evidenceId,
      provenance: {
        kind: "official_provider",
        source: "authorized-test",
        observed_at: "2026-07-31T02:59:00Z",
      },
      actions: [{ kind: "add_to_watch" }],
    }],
    ...overrides,
  };
}

describe("expired registration evidence recovery", () => {
  it("accepts one exact refreshed identity with unchanged observed status and a new evidence id", () => {
    const refreshed = train("sold_out", "new-evidence", { train_number: "330" });
    const result = recoverRefreshedRegistrationTrain(
      train("sold_out", "old-evidence"),
      [refreshed],
      "standard",
    );
    expect(result).toEqual({ ok: true, train: refreshed });
  });

  it.each([
    ["official_page_browser_companion", "korail-official-browser-companion"],
    ["user_confirmed_official_page", "official-page-user-confirmation"],
  ])("accepts a fresh %s observation", (kind, source) => {
    const refreshed = train("sold_out", "new-evidence");
    const refreshedSeat = refreshed.seat_classes.at(0);
    if (!refreshedSeat) throw new Error("test seat fixture is missing");
    refreshedSeat.provenance = {
      kind,
      source,
      observed_at: new Date(Date.now() - 30_000).toISOString(),
      fresh_until: new Date(Date.now() + 60_000).toISOString(),
    };
    expect(recoverRefreshedRegistrationTrain(
      train("sold_out", "old-evidence"),
      [refreshed],
      "standard",
    )).toEqual({ ok: true, train: refreshed });
  });

  it("fails closed when status changes", () => {
    const result = recoverRefreshedRegistrationTrain(
      train("sold_out", "old-evidence"),
      [train("available", "new-evidence")],
      "standard",
    );
    expect(result).toMatchObject({ ok: false, reason: "status_changed" });
  });

  it("fails closed for an unobserved refresh or reused evidence", () => {
    const unobserved = train("sold_out", "new-evidence");
    const unobservedSeat = unobserved.seat_classes.at(0);
    if (!unobservedSeat) throw new Error("test seat fixture is missing");
    unobservedSeat.provenance = {
      kind: "not_observed",
      source: "authorized-test",
      observed_at: "2026-07-31T02:59:00Z",
    };
    expect(recoverRefreshedRegistrationTrain(
      train("sold_out", "old-evidence"),
      [unobserved],
      "standard",
    )).toMatchObject({ ok: false, reason: "not_observed" });
    expect(recoverRefreshedRegistrationTrain(
      train("sold_out", "same-evidence"),
      [train("sold_out", "same-evidence")],
      "standard",
    )).toMatchObject({ ok: false, reason: "evidence_missing" });
  });

  it("fails closed for expired or incorrectly sourced official-page evidence", () => {
    const expired = train("sold_out", "new-evidence");
    const expiredSeat = expired.seat_classes.at(0);
    if (!expiredSeat) throw new Error("test seat fixture is missing");
    expiredSeat.provenance = {
      kind: "official_page_browser_companion",
      source: "korail-official-browser-companion",
      observed_at: new Date(Date.now() - 120_000).toISOString(),
      fresh_until: new Date(Date.now() - 60_000).toISOString(),
    };
    expect(recoverRefreshedRegistrationTrain(
      train("sold_out", "old-evidence"),
      [expired],
      "standard",
    )).toMatchObject({ ok: false, reason: "not_observed" });

    expiredSeat.provenance = {
      ...expiredSeat.provenance,
      source: "untrusted-source",
      fresh_until: new Date(Date.now() + 60_000).toISOString(),
    };
    expect(recoverRefreshedRegistrationTrain(
      train("sold_out", "old-evidence"),
      [expired],
      "standard",
    )).toMatchObject({ ok: false, reason: "not_observed" });
  });
});
