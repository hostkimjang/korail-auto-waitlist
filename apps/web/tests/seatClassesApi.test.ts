import { beforeEach, describe, expect, expectTypeOf, it, vi } from "vitest";

import { normalizeSeatClasses } from "../src/api/seatClasses";
import type {
  NormalizedSeatClass as CompatibilityNormalizedSeatClass,
} from "../src/api/seatClasses";
import type { NormalizedSeatClass } from "../src/domain/seatClasses";

function seatAt(seats: readonly NormalizedSeatClass[], index = 0): NormalizedSeatClass {
  const seat = seats[index];
  if (!seat) throw new Error(`Expected normalized seat class at index ${index}`);
  return seat;
}

describe("seat class API normalization contract", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("normalizes missing or unproven seat data to two explicit unknown classes", () => {
    const seats = normalizeSeatClasses({ provider: "korail", official_booking_url: "https://www.korail.com/ticket/search" });
    expectTypeOf<CompatibilityNormalizedSeatClass>().toEqualTypeOf<NormalizedSeatClass>();
    expect(seats.map((seat) => [seat.seat_class, seat.status, seat.provenance.kind])).toEqual([
      ["standard", "unknown", "not_observed"],
      ["first", "unknown", "not_observed"],
    ]);
    expect(seatAt(seats).actions[0]).toMatchObject({ kind: "official_check" });
  });

  it("preserves a supported non-observation reason and hides arbitrary backend detail", () => {
    const supported = normalizeSeatClasses({
      provider: "korail",
      seat_classes: [{
        seat_class: "standard",
        status: "unknown",
        provenance: { kind: "not_observed", reason: "provider_access_restricted" },
      }],
    });
    const arbitrary = normalizeSeatClasses({
      provider: "korail",
      seat_classes: [{
        seat_class: "standard",
        status: "unknown",
        provenance: { kind: "not_observed", reason: "raw_upstream_error_text" },
      }],
    });

    expect(seatAt(supported).provenance.reason).toBe("provider_access_restricted");
    expect(seatAt(arbitrary).provenance.reason).toBe("public_api_not_available");
  });

  it("fails closed when a positive seat state has no authorized provenance", () => {
    const seats = normalizeSeatClasses({
      provider: "korail",
      official_booking_url: "https://www.korail.com/ticket/search",
      seat_classes: [{ seat_class: "standard", status: "available", provenance: { kind: "not_observed" } }],
    });
    expect(seats[0]).toMatchObject({ status: "unknown", provenance: { reason: "invalid_provider_provenance" } });
  });

  it("keeps a proven seat observation and sanitizes its actions", () => {
    const seats = normalizeSeatClasses({
      provider: "korail",
      official_booking_url: "https://www.korail.com/ticket/search",
      seat_classes: [{
        seat_class: "standard",
        status: "limited",
        fare: 59800,
        fare_currency: "KRW",
        registration_evidence_id: "10000000-0000-4000-8000-000000000001",
        provenance: { kind: "official_provider", source: "authorized-test", observed_at: "2026-08-01T00:00:00Z" },
        actions: [{ kind: "official_check", url: "http://insecure.example" }, { kind: "add_to_watch", url: "https://ignored.example" }],
      }],
    });
    expect(seats[0]).toMatchObject({ status: "limited", fare: 59800, actions: [{ kind: "add_to_watch", url: null }] });
    expect(seatAt(seats, 1).status).toBe("unknown");
  });

  it("preserves standing-only as observed without promoting it to direct availability", () => {
    const seat = seatAt(normalizeSeatClasses({
      provider: "korail",
      official_booking_url: "https://www.korail.com/ticket/search",
      seat_classes: [{
        seat_class: "standard",
        status: "standing_only",
        registration_evidence_id: "evidence-standing-only",
        provenance: {
          kind: "official_provider",
          source: "korail-official-page-browser",
          observed_at: "2026-08-15T00:00:00Z",
        },
        actions: [
          { kind: "official_check", url: "https://www.korail.com/ticket/search" },
          { kind: "add_to_watch", url: null },
        ],
      }],
    }));

    expect(seat).toMatchObject({
      status: "standing_only",
      actions: [
        { kind: "official_check" },
        { kind: "add_to_watch", url: null },
      ],
    });
  });

  it("keeps an observed official status visible but disables registration without evidence", () => {
    const seat = seatAt(normalizeSeatClasses({
      provider: "srt",
      official_booking_url: "https://etk.srail.kr",
      seat_classes: [{
        seat_class: "standard",
        status: "available",
        provenance: {
          kind: "official_provider",
          source: "authorized-test",
          observed_at: "2026-08-01T00:00:00Z",
        },
        actions: [{ kind: "add_to_watch" }],
      }],
    }));

    expect(seat.status).toBe("available");
    expect(seat.actions).toEqual([]);
    expect(seat.registration_evidence_id).toBeNull();
    expect(seat.registration_evidence_error).toMatch(/등록 근거/);
  });

  it("does not show a registration warning when only official booking is offered", () => {
    const seat = seatAt(normalizeSeatClasses({
      provider: "korail",
      official_booking_url: "https://www.korail.com/ticket/search/general",
      seat_classes: [{
        seat_class: "standard",
        status: "available",
        provenance: {
          kind: "official_provider",
          source: "korail-official-page-browser",
          observed_at: "2026-08-01T00:00:00Z",
        },
        actions: [{
          kind: "official_check",
          url: "https://www.korail.com/ticket/search/general",
        }],
      }],
    }));

    expect(seat.status).toBe("available");
    expect(seat.actions).toHaveLength(1);
    expect(seat.registration_evidence_error).toBeNull();
  });

  it("keeps an observed unknown state without inventing availability", () => {
    const seats = normalizeSeatClasses({
      provider: "srt",
      official_booking_url: "https://etk.srail.kr",
      seat_classes: [{
        seat_class: "standard",
        status: "unknown",
        provenance: { kind: "official_provider", source: "srtrain-2.6.7-accountless", observed_at: "2026-08-01T00:00:00Z" },
        actions: [{ kind: "official_check", url: "https://etk.srail.kr" }],
      }],
    });

    expect(seats[0]).toMatchObject({
      status: "unknown",
      provenance: { kind: "official_provider", source: "srtrain-2.6.7-accountless" },
    });
  });

  it("accepts only bounded-TTL, fixed-source official-page user confirmations", () => {
    const receivedAt = 1_000;
    vi.spyOn(performance, "now").mockReturnValue(receivedAt);
    vi.spyOn(Date, "now").mockReturnValue(Date.parse("2099-01-01T00:00:00Z"));
    const observedAt = "2020-01-01T00:00:00Z";
    const freshUntil = "2020-01-01T00:05:00Z";
    const confirmed = normalizeSeatClasses({
      provider: "korail",
      seat_classes: [{
        seat_class: "standard",
        status: "sold_out",
        provenance: {
          kind: "user_confirmed_official_page",
          source: "official-page-user-confirmation",
          observed_at: observedAt,
          fresh_until: freshUntil,
        },
        actions: [{ kind: "add_to_watch" }],
      }],
    });
    const missingExpiry = normalizeSeatClasses({
      provider: "korail",
      seat_classes: [{
        seat_class: "standard",
        status: "available",
        provenance: {
          kind: "user_confirmed_official_page",
          source: "official-page-user-confirmation",
          observed_at: observedAt,
        },
      }],
    });
    const invalidWindow = normalizeSeatClasses({
      provider: "korail",
      seat_classes: [{
        seat_class: "standard",
        status: "available",
        provenance: {
          kind: "user_confirmed_official_page",
          source: "official-page-user-confirmation",
          observed_at: observedAt,
          fresh_until: observedAt,
        },
      }],
    });

    expect(confirmed[0]).toMatchObject({
      status: "sold_out",
      provenance: {
        kind: "user_confirmed_official_page",
        source: "official-page-user-confirmation",
        client_freshness: { received_monotonic_ms: receivedAt, ttl_ms: 5 * 60_000 },
      },
    });
    expect(missingExpiry[0]).toMatchObject({ status: "unknown", provenance: { reason: "invalid_provider_provenance" } });
    expect(invalidWindow[0]).toMatchObject({ status: "unknown", provenance: { reason: "invalid_provider_provenance" } });
  });

  it("accepts a bounded fixed-source KORAIL browser companion snapshot", () => {
    vi.spyOn(performance, "now").mockReturnValue(2_000);
    const seats = normalizeSeatClasses({
      provider: "korail",
      seat_classes: [{
        seat_class: "standard",
        status: "limited",
        registration_evidence_id: "10000000-0000-4000-8000-000000000001",
        provenance: {
          kind: "official_page_browser_companion",
          source: "korail-official-browser-companion",
          observed_at: "2026-07-30T00:00:00Z",
          fresh_until: "2026-07-30T00:02:00Z",
        },
        actions: [{ kind: "add_to_watch" }],
      }],
    });

    expect(seats[0]).toMatchObject({
      status: "limited",
      provenance: {
        kind: "official_page_browser_companion",
        client_freshness: { received_monotonic_ms: 2_000, ttl_ms: 2 * 60_000 },
      },
    });
  });

  it.each([
    { source: "   ", observed_at: "2026-08-01T00:00:00Z" },
    { source: "authorized-test", observed_at: "2026-08-01T00:00:00" },
    { source: "authorized-test", observed_at: "not-a-date" },
  ])("fails closed for malformed observed provenance %#", (provenance) => {
    const seats = normalizeSeatClasses({
      provider: "korail",
      official_booking_url: "https://www.korail.com/ticket/search",
      seat_classes: [{
        seat_class: "standard",
        status: "available",
        provenance: { kind: "official_provider", ...provenance },
        actions: [{ kind: "official_check", url: "https://www.korail.com/ticket/search" }],
      }],
    });
    expect(seats[0]).toMatchObject({ status: "unknown", provenance: { reason: "invalid_provider_provenance" } });
  });

  it("preserves observed unavailable and does not repair a missing official action URL", () => {
    const seats = normalizeSeatClasses({
      provider: "korail",
      official_booking_url: "https://www.korail.com/ticket/search",
      seat_classes: [{
        seat_class: "standard",
        status: "unavailable",
        registration_evidence_id: "10000000-0000-4000-8000-000000000099",
        provenance: { kind: "official_provider", source: "authorized-test", observed_at: "2026-08-01T00:00:00+09:00" },
        actions: [{ kind: "official_check" }, { kind: "add_to_watch" }],
      }],
    });
    expect(seats[0]).toMatchObject({ status: "unavailable", actions: [{ kind: "add_to_watch", url: null }] });
  });

  it("preserves an empty provider action set and rejects arbitrary HTTPS action hosts", () => {
    const empty = normalizeSeatClasses({
      provider: "korail",
      official_booking_url: "https://www.korail.com/ticket/search",
      seat_classes: [{
        seat_class: "standard",
        status: "available",
        provenance: { kind: "official_provider", source: "authorized-test", observed_at: "2026-08-01T00:00:00+09:00" },
        actions: [],
      }],
    });
    const arbitraryHost = normalizeSeatClasses({
      provider: "srt",
      official_booking_url: "https://etk.srail.kr",
      seat_classes: [{
        seat_class: "standard",
        status: "available",
        provenance: { kind: "official_provider", source: "authorized-test", observed_at: "2026-08-01T00:00:00+09:00" },
        actions: [{ kind: "official_check", url: "https://evil.example/phishing" }],
      }],
    });
    expect(seatAt(empty).actions).toEqual([]);
    expect(seatAt(arbitraryHost).actions).toEqual([]);
  });
});
