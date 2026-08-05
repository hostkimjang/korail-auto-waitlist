import { describe, expect, it } from "vitest";

import { ApiError } from "../src/api/client";
import {
  parseWatchCandidateReadDto,
  parseWatchReadDto,
} from "../src/api/watchReadDto";

const WATCH_ROW = {
  id: " watch-1 ",
  provider: " korail ",
  status: "watching",
  origin: " 서울 ",
  destination: " 부산 ",
  travel_date: "2026-08-08",
};

describe("watch read DTO boundary", () => {
  it("normalizes the required watch identity and exposes only known fields", () => {
    const parsed = parseWatchReadDto({
      ...WATCH_ROW,
      arbitrary_server_field: "must-not-leak",
      candidates: "not-an-array",
    });

    expect(parsed).toMatchObject({
      id: "watch-1",
      provider: "KORAIL",
      status: "watching",
      origin: "서울",
      destination: "부산",
      travel_date: "2026-08-08",
      candidates: [],
    });
    expect(Object.hasOwn(parsed, "arbitrary_server_field")).toBe(false);
  });

  it("rejects unsupported identity values and impossible service dates with the stable error", () => {
    const invalidRows = [
      { ...WATCH_ROW, provider: "untrusted" },
      { ...WATCH_ROW, status: "WATCHING" },
      { ...WATCH_ROW, travel_date: "2026-02-30" },
    ];

    for (const row of invalidRows) {
      expect(() => parseWatchReadDto(row)).toThrow(
        new ApiError("대기 작업 응답 형식을 확인할 수 없습니다."),
      );
    }
  });

  it("drops candidates whose required identity or schedule contract is malformed", () => {
    const candidate = {
      id: "candidate-1",
      train_number: "KTX 001",
      departure_at: "2026-08-08T10:00:00+09:00",
      arrival_at: "2026-08-08T13:00:00+09:00",
      seat_class: "standard",
      priority: 1,
    };

    expect(parseWatchCandidateReadDto({ ...candidate, id: " " })).toBeNull();
    expect(parseWatchCandidateReadDto({ ...candidate, departure_at: "2026-08-08 10:00" }))
      .toBeNull();
    expect(parseWatchCandidateReadDto({ ...candidate, arrival_at: "not-a-time" })).toBeNull();
    expect(parseWatchCandidateReadDto({ ...candidate, seat_class: "suite" })).toBeNull();
    expect(parseWatchCandidateReadDto({ ...candidate, priority: 0 })).toBeNull();
  });

  it("preserves known nested evidence inputs as unknown without leaking arbitrary keys", () => {
    const latestObservation = { status: "available", source: "authorized-provider" };
    const registrationEvidence = { id: "evidence-1", provenance: { kind: "official_provider" } };
    const parsed = parseWatchCandidateReadDto({
      id: " candidate-1 ",
      train_number: " KTX 001 ",
      departure_at: "2026-08-08T10:00:00+09:00",
      arrival_at: null,
      seat_class: "standard",
      priority: 1,
      latest_observation: latestObservation,
      registration_evidence: registrationEvidence,
      arbitrary_candidate_field: "must-not-leak",
    });

    expect(parsed).toMatchObject({
      id: "candidate-1",
      train_number: "KTX 001",
      arrival_at: null,
      latest_observation: latestObservation,
      registration_evidence: registrationEvidence,
    });
    expect(Object.hasOwn(parsed ?? {}, "arbitrary_candidate_field")).toBe(false);
  });
});
