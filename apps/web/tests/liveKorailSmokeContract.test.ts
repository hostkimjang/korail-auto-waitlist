import { describe, expect, it } from "vitest";

import {
  assertNoLiveKorailWatchRegistration,
  expectedLiveKorailActions,
  failureCauseFromLiveTimetable,
  parseFreshLiveKorailTrain,
  parseLiveKorailCapability,
  parseLiveKorailPreflight,
  sanitizedLiveKorailFailureCause,
} from "../src/features/new-wait/liveKorailSmokeContract";

const observedAt = "2026-07-30T10:00:01Z";

function seat(
  seatClass: "standard" | "first",
  status: string,
  seatMonitoring = false,
): object {
  const actions = ["available", "limited", "standing_plus_seat"].includes(status)
    ? [
        { kind: "official_check", url: "https://www.korail.com/ticket/search/list" },
        ...(seatMonitoring ? [{ kind: "add_to_watch", url: null }] : []),
      ]
    : status === "waitlist_available"
      ? [
          { kind: "official_waitlist", url: "https://www.korail.com/ticket/search/list" },
          ...(seatMonitoring ? [{ kind: "add_to_watch", url: null }] : []),
        ]
      : status === "sold_out" && seatMonitoring
        ? [{ kind: "add_to_watch", url: null }]
        : [];
  const requiresEvidence = seatMonitoring && [
    "available",
    "limited",
    "standing_plus_seat",
    "sold_out",
    "waitlist_available",
  ].includes(status);
  return {
    seat_class: seatClass,
    status,
    provenance: {
      kind: "official_provider",
      source: "korail-official-page-browser",
      observed_at: observedAt,
    },
    registration_evidence_id: requiresEvidence ? `evidence-${seatClass}` : null,
    actions,
  };
}

function timetableItem(
  standardStatus = "available",
  firstStatus = "sold_out",
): object {
  return {
    provider: "korail",
    train_number: "43",
    origin: "서울",
    destination: "부산",
    departure_at: "2026-07-31T12:30:00+09:00",
    official_booking_url: "https://www.korail.com/ticket/search/list",
    seat_classes: [seat("standard", standardStatus), seat("first", firstStatus)],
  };
}

describe("KORAIL live smoke contract", () => {
  it("strictly parses the KORAIL ready and cooldown preflight states", () => {
    expect(parseLiveKorailPreflight([
      {
        provider: "korail",
        source: "korail_browser",
        state: "ready",
        cause: null,
        retry_after_seconds: null,
      },
    ])).toEqual({ state: "ready", cause: null, retryAfterSeconds: null });
    expect(parseLiveKorailPreflight([
      {
        provider: "korail",
        source: "korail_browser",
        state: "cooldown",
        cause: "provider_access_restricted",
        retry_after_seconds: 12.1,
      },
    ])).toEqual({
      state: "cooldown",
      cause: "provider_access_restricted",
      retryAfterSeconds: 13,
    });
    expect(() => parseLiveKorailPreflight([])).toThrow("invalid_status_response");
    expect(() => parseLiveKorailPreflight([
      {
        provider: "korail",
        source: "korail_browser",
        state: "cooldown",
        cause: "raw upstream body",
        retry_after_seconds: 30,
      },
    ])).toThrow("invalid_status_response");
  });

  it("selects the single official KORAIL execution capability", () => {
    expect(parseLiveKorailCapability([
      {
        provider: "korail",
        timetable: true,
        official_booking_link: true,
      official_waitlist_link: false,
      seat_monitoring: false,
      reservation_once: true,
        experimental: false,
        enabled: true,
      },
      {
        provider: "korail",
        timetable: false,
        official_booking_link: true,
        official_waitlist_link: false,
        seat_monitoring: false,
        reservation_once: false,
        experimental: true,
        enabled: true,
      },
    ])).toEqual({
      enabled: true,
      timetable: true,
      officialBookingLink: true,
      officialWaitlistLink: false,
      seatMonitoring: false,
      reservationOnce: true,
    });
    expect(() => parseLiveKorailCapability([])).toThrow("invalid_capability_response");
    expect(() => parseLiveKorailCapability([{
      provider: "korail",
      timetable: true,
      official_booking_link: true,
      official_waitlist_link: false,
      seat_monitoring: true,
      reservation_once: false,
      experimental: false,
      enabled: true,
    }])).not.toThrow();
  });

  it("accepts watch evidence only when KORAIL monitoring capability is enabled", () => {
    const monitored = [{
      ...timetableItem(),
      seat_classes: [seat("standard", "available", true), seat("first", "sold_out", true)],
    }];
    expect(parseFreshLiveKorailTrain(
      monitored,
      Date.parse("2026-07-30T10:00:00Z"),
      undefined,
      true,
    )).toMatchObject({
      standard: { status: "available", registrationEvidencePresent: true },
      first: { status: "sold_out", registrationEvidencePresent: true },
    });
    expect(() => parseFreshLiveKorailTrain(
      monitored,
      Date.parse("2026-07-30T10:00:00Z"),
      undefined,
      false,
    )).toThrow("no_official_provider_observation");
  });

  it("requires a fresh exact-journey pair with official URLs and no watch evidence", () => {
    const payload = [timetableItem()];
    const journey = {
      origin: "서울",
      destination: "부산",
      departureDate: "2026-07-31",
      departureFrom: "12:00",
      departureTo: "18:00",
    };
    expect(parseFreshLiveKorailTrain(payload, Date.parse("2026-07-30T10:00:00Z")))
      .toMatchObject({
        trainNumber: "43",
        departureAt: "2026-07-31T12:30:00+09:00",
        standard: { status: "available" },
        first: { status: "sold_out" },
      });
    expect(parseFreshLiveKorailTrain(
      payload,
      Date.parse("2026-07-30T10:00:00Z"),
      journey,
    )).toMatchObject({ trainNumber: "43" });
    expect(() => parseFreshLiveKorailTrain(payload, Date.parse("2026-07-30T10:00:02Z")))
      .toThrow("no_official_provider_observation");
    expect(() => parseFreshLiveKorailTrain([{
      ...payload[0],
      seat_classes: [
        seat("standard", "available"),
        {
          ...seat("first", "sold_out"),
          provenance: { kind: "official_provider", source: "legacy-source", observed_at: observedAt },
        },
      ],
    }], Date.parse("2026-07-30T10:00:00Z")))
      .toThrow("no_official_provider_observation");
    expect(() => parseFreshLiveKorailTrain([{
      ...timetableItem(),
      origin: "대전",
    }], Date.parse("2026-07-30T10:00:00Z"), journey))
      .toThrow("no_official_provider_observation");
    expect(() => parseFreshLiveKorailTrain([{
      ...timetableItem(),
      seat_classes: [
        { ...seat("standard", "available"), registration_evidence_id: "watch-evidence" },
        seat("first", "sold_out"),
      ],
    }], Date.parse("2026-07-30T10:00:00Z"), journey))
      .toThrow("no_official_provider_observation");
    expect(() => parseFreshLiveKorailTrain([{
      ...timetableItem(),
      seat_classes: [
        {
          ...seat("standard", "available"),
          actions: [{ kind: "official_check", url: "https://attacker.invalid/ticket" }],
        },
        seat("first", "sold_out"),
      ],
    }], Date.parse("2026-07-30T10:00:00Z"), journey))
      .toThrow("no_official_provider_observation");
    expect(() => parseFreshLiveKorailTrain(
      [timetableItem("not_offered", "not_offered")],
      Date.parse("2026-07-30T10:00:00Z"),
      journey,
    )).toThrow("no_official_provider_observation");
    expect(() => assertNoLiveKorailWatchRegistration(payload)).not.toThrow();
    expect(() => assertNoLiveKorailWatchRegistration([
      timetableItem(),
      {
        ...timetableItem(),
        train_number: "45",
        seat_classes: [
          {
            ...seat("standard", "sold_out"),
            registration_evidence_id: "watch-evidence",
            actions: [{ kind: "add_to_watch", url: null }],
          },
          seat("first", "sold_out"),
        ],
      },
    ])).toThrow("unexpected_watch_registration_evidence");
  });

  it("derives CTA requirements from status and execution capability", () => {
    expect(expectedLiveKorailActions("available", false)).toEqual(["official_booking"]);
    expect(expectedLiveKorailActions("limited", false)).toEqual(["official_booking"]);
    expect(expectedLiveKorailActions("standing_plus_seat", false)).toEqual(["official_booking"]);
    expect(expectedLiveKorailActions("available", true)).toEqual([
      "official_booking",
      "add_to_watch",
    ]);
    expect(expectedLiveKorailActions("limited", true)).toEqual([
      "official_booking",
      "add_to_watch",
    ]);
    expect(expectedLiveKorailActions("standing_plus_seat", true)).toEqual([
      "official_booking",
      "add_to_watch",
    ]);
    expect(expectedLiveKorailActions("waitlist_available", false)).toEqual([
      "official_waitlist",
    ]);
    expect(expectedLiveKorailActions("waitlist_available", true)).toEqual([
      "official_waitlist",
      "add_to_watch",
    ]);
    expect(expectedLiveKorailActions("sold_out", false)).toEqual([]);
    expect(expectedLiveKorailActions("sold_out", true)).toEqual(["add_to_watch"]);
    expect(expectedLiveKorailActions("not_offered", true)).toEqual([]);
  });

  it("reduces artifacts to allowed causes only", () => {
    expect(failureCauseFromLiveTimetable([{
      provider: "korail",
      seat_classes: [{
        provenance: { kind: "not_observed", reason: "provider_access_restricted" },
      }],
    }])).toBe("provider_access_restricted");
    expect(sanitizedLiveKorailFailureCause("raw provider response")).toBe(
      "no_official_provider_observation",
    );
  });
});
