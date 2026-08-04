import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/api/client";
import {
  buildWatchCreatePayloads,
  cancelWatch,
  createWatch,
  fetchWatches,
  mapWatch,
  startWatch,
} from "../src/api/watches";

const WATCH_DTO = {
  id: "watch-1",
  provider: "korail",
  origin: "서울",
  destination: "부산",
  travel_date: "2026-08-08",
  time_from: "10:00:00",
  time_to: "13:00:00",
  train_numbers: ["KTX 001"],
  seat_class: "standard",
  status: "watching",
  candidates: [{
    id: "candidate-1",
    priority: 1,
    train_number: "KTX 001",
    departure_at: "2026-08-08T10:00:00+09:00",
    arrival_at: "2026-08-08T13:00:00+09:00",
    seat_class: "standard",
  }],
};

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  Object.defineProperty(document, "cookie", { writable: true, value: "rail_csrf=csrf-token" });
});

describe("watch API boundary", () => {
  it("rejects malformed list envelopes and rows instead of fabricating watch state", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(jsonResponse({ items: [] }))
      .mockResolvedValueOnce(jsonResponse([{ ...WATCH_DTO, id: null }]))
      .mockResolvedValueOnce(jsonResponse([{ ...WATCH_DTO, provider: "untrusted" }])));

    await expect(fetchWatches()).rejects.toThrow("대기 작업 목록 응답 형식");
    await expect(fetchWatches()).rejects.toThrow("대기 작업 응답 형식");
    await expect(fetchWatches()).rejects.toThrow("대기 작업 응답 형식");
  });

  it("fails malformed registration provenance closed without inventing availability", () => {
    const mapped = mapWatch({
      ...WATCH_DTO,
      candidates: [{
        ...WATCH_DTO.candidates[0],
        registration_evidence: {
          id: "10000000-0000-4000-8000-000000000001",
          status: "available",
          provenance: { kind: "official_provider", observed_at: "2026-08-08T00:00:00Z" },
          created_at: "2026-08-08T00:00:01Z",
          registration_valid_until: "2026-08-08T00:05:01Z",
        },
      }],
    });

    expect(mapped.registrationEvidenceLabel).not.toContain("예매 가능");
    expect(mapped.registrationEvidenceLabel).toContain("일반실");
    expect(mapped.seatFoundObservation).toBeNull();
  });

  it("removes non-official action URLs from both legacy and view-model fields", () => {
    const mapped = mapWatch({
      ...WATCH_DTO,
      official_booking_url: "https://attacker.example/pay",
    });

    expect(mapped.official_booking_url).toBeNull();
    expect(mapped.officialBookingUrl).toBeNull();
  });

  it.each([
    {
      name: "missing source",
      provider: "korail",
      observation: {
        status: "available",
        observed_at: "2030-08-08T00:00:00Z",
        fresh_until: "2030-08-08T00:05:00Z",
      },
    },
    {
      name: "mock source on an official provider",
      provider: "korail",
      observation: {
        status: "available",
        source: "mock",
        observed_at: "2030-08-08T00:00:00Z",
        fresh_until: "2030-08-08T00:05:00Z",
      },
    },
    {
      name: "official source on a mock provider",
      provider: "mock",
      observation: {
        status: "available",
        source: "authorized-provider",
        observed_at: "2030-08-08T00:00:00Z",
        fresh_until: "2030-08-08T00:05:00Z",
      },
    },
    {
      name: "non-increasing freshness window",
      provider: "korail",
      observation: {
        status: "available",
        source: "authorized-provider",
        observed_at: "2030-08-08T00:05:00Z",
        fresh_until: "2030-08-08T00:05:00Z",
      },
    },
  ])("fails $name latest observations closed", ({ provider, observation }) => {
    const mapped = mapWatch({
      ...WATCH_DTO,
      provider,
      status: "seat_found",
      candidates: [{
        ...WATCH_DTO.candidates[0],
        latest_observation: observation,
      }],
    });

    expect(mapped.seatFoundObservation).toBeNull();
    expect(mapped.seatEvidenceLabel).not.toContain("예매 가능");
  });

  it("preserves complete official and mock latest-observation provenance", () => {
    const observation = {
      status: "available",
      observed_at: "2030-08-08T00:00:00Z",
      fresh_until: "2030-08-08T00:05:00Z",
    };
    const official = mapWatch({
      ...WATCH_DTO,
      status: "seat_found",
      candidates: [{
        ...WATCH_DTO.candidates[0],
        latest_observation: { ...observation, source: "korail-pydoll-reservation" },
      }],
    });
    const mock = mapWatch({
      ...WATCH_DTO,
      provider: "mock",
      status: "seat_found",
      candidates: [{
        ...WATCH_DTO.candidates[0],
        latest_observation: { ...observation, source: "mock" },
      }],
    });

    expect(official.seatFoundObservation).toMatchObject({
      kind: "official_provider",
      source: "korail-pydoll-reservation",
    });
    expect(mock.seatFoundObservation).toMatchObject({ kind: "mock", source: "mock" });
  });

  it("ignores a priority-only malformed candidate before evidence and CTA projection", () => {
    const malformedCandidate = {
      priority: 1,
      train_number: "UNTRUSTED 999",
      departure_at: "2030-08-08T09:00:00+09:00",
      arrival_at: "2030-08-08T12:00:00+09:00",
      seat_class: "standard",
      registration_evidence: {
        id: "10000000-0000-4000-8000-000000000099",
        status: "available",
        provenance: {
          kind: "official_provider",
          source: "authorized-provider",
          observed_at: "2030-08-08T00:00:00Z",
        },
        created_at: "2030-08-08T00:00:00Z",
        registration_valid_until: "2030-08-08T00:05:00Z",
      },
      latest_observation: {
        status: "available",
        source: "authorized-provider",
        observed_at: "2030-08-08T00:00:00Z",
        fresh_until: "2030-08-08T00:05:00Z",
      },
    };
    const validCandidate = {
      ...WATCH_DTO.candidates[0],
      id: " candidate-2 ",
      train_number: " KTX 002 ",
      priority: 2,
    };

    const mapped = mapWatch({
      ...WATCH_DTO,
      status: "seat_found",
      candidates: [malformedCandidate, validCandidate],
    });

    expect(mapped.candidates).toEqual([{
      id: "candidate-2",
      train_number: "KTX 002",
      departure_at: "2026-08-08T10:00:00+09:00",
      arrival_at: "2026-08-08T13:00:00+09:00",
      seat_class: "standard",
      priority: 2,
    }]);
    expect(mapped.train).toBe("KTX 002");
    expect(mapped.departure).toBe("10:00");
    expect(mapped.arrival).toBe("13:00");
    expect(mapped.registrationEvidenceLabel).not.toContain("예매 가능");
    expect(mapped.seatFoundObservation).toBeNull();
    expect(mapped.reservationCandidateContexts).toEqual({
      "candidate-2": {
        train: "KTX 002",
        seatClassLabel: "일반실",
        date: "8월 8일 (토)",
        departure: "10:00",
        arrival: "13:00",
      },
    });
  });

  it("returns an explicit view model without leaking raw DTO keys", () => {
    const mapped = mapWatch({
      ...WATCH_DTO,
      arbitrary_server_field: "must-not-leak",
      payment_deadline: "not-a-timestamp",
      created_at: "2026-08-07T15:00:00Z",
      updated_at: "2026-08-08 00:01:00",
      official_booking_url: "https://www.korail.com/ticket/search",
      reservation_policy: "unsupported-policy",
      candidates: [{
        ...WATCH_DTO.candidates[0],
        id: " candidate-1 ",
        train_number: " KTX 001 ",
        arrival_at: null,
        arbitrary_candidate_field: "must-not-leak",
      }],
    });

    expect(Object.hasOwn(mapped, "arbitrary_server_field")).toBe(false);
    expect(mapped).toMatchObject({
      payment_deadline: null,
      created_at: "2026-08-07T15:00:00Z",
      updated_at: null,
      reservation_policy: "notify_only",
      reservationPolicy: "notify_only",
      candidates: [{
        id: "candidate-1",
        train_number: "KTX 001",
        departure_at: "2026-08-08T10:00:00+09:00",
        arrival_at: null,
        seat_class: "standard",
        priority: 1,
      }],
    });
    expect(Object.hasOwn(mapped.candidates[0] ?? {}, "arbitrary_candidate_field")).toBe(false);
  });

  it("builds one evidence-bound payload per selected provider, train, and seat class", () => {
    const payloads = buildWatchCreatePayloads({
      providers: ["KORAIL", "SRT"],
      origin: "서울",
      origin_node_id: "N-SEOUL",
      destination: "부산",
      destination_node_id: "N-BUSAN",
      date: "2026-08-08",
      selectedWeekdays: [6],
      passengers: 1,
    }, [
      {
        provider: "KORAIL",
        train_number: "KTX 001",
        departure_at: "2026-08-08T10:00:00+09:00",
        arrival_at: "2026-08-08T13:00:00+09:00",
        selected_seat_class: "standard",
        seat_classes: [{
          seat_class: "standard",
          registration_evidence_id: "10000000-0000-4000-8000-000000000001",
        }],
      },
      {
        provider: "SRT",
        train_number: "SRT 101",
        departure_at: "2026-08-08T11:00:00+09:00",
        arrival_at: "2026-08-08T13:30:00+09:00",
        selected_seat_class: "first",
        seat_classes: [{
          seat_class: "first",
          registration_evidence_id: "20000000-0000-4000-8000-000000000101",
        }],
      },
    ], ["channel-1"]);

    expect(payloads.map((payload) => ({
      provider: payload.provider,
      train: payload.train_numbers[0],
      seatClass: payload.seat_class,
      evidenceId: payload.candidates[0]?.registration_evidence_id,
    }))).toEqual([
      {
        provider: "korail",
        train: "KTX 001",
        seatClass: "standard",
        evidenceId: "10000000-0000-4000-8000-000000000001",
      },
      {
        provider: "srt",
        train: "SRT 101",
        seatClass: "first",
        evidenceId: "20000000-0000-4000-8000-000000000101",
      },
    ]);
  });

  it("uses stable evidence/watch idempotency keys and maps mutation responses", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => jsonResponse(WATCH_DTO, 201));
    vi.stubGlobal("fetch", fetchMock);
    const evidenceId = "10000000-0000-4000-8000-000000000001";

    await createWatch({ candidates: [{ registration_evidence_id: evidenceId }] });
    await startWatch(WATCH_DTO.id);
    await cancelWatch(WATCH_DTO.id);

    expect(fetchMock.mock.calls.map(([, options]) => (
      new Headers((options as RequestInit).headers).get("Idempotency-Key")
    ))).toEqual([
      `watch-create:${evidenceId}`,
      `watch-start:${WATCH_DTO.id}`,
      `watch-cancel:${WATCH_DTO.id}`,
    ]);
    expect(fetchMock.mock.calls.every(([, options]) => (
      (options as RequestInit).credentials === "include"
    ))).toBe(true);
  });

  it("annotates invalid create responses with the watch operation", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ id: "partial" }, 201)));

    const error = await createWatch({ provider: "korail" }).catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ operation: "watch.create" });
  });
});
