import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/api/client";
import { mapTimetable } from "../src/api/timetables";
import {
  buildWatchCreatePayload,
  buildWatchCreatePayloads,
  cancelWatch,
  createWatch,
  fetchWatches,
  mapWatch,
  startWatch,
  updateWatch,
  type MappedWatch,
  type WatchCreateForm,
  type WatchReadModel,
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

const apiWatch = {
  id: "watch-1",
  provider: "korail",
  origin: "서울",
  destination: "부산",
  travel_date: "2026-07-31",
  time_from: "13:18:00",
  time_to: "15:59:00",
  train_numbers: ["KTX 033"],
  seat_class: "standard",
  candidates: [{
    id: "candidate-1",
    train_number: "KTX 033",
    departure_at: "2026-07-31T13:18:00+09:00",
    arrival_at: "2026-07-31T15:59:00+09:00",
    seat_class: "standard",
    priority: 1,
    state: "observed",
    registration_evidence: {
      id: "10000000-0000-4000-8000-000000000001",
      status: "sold_out",
      provenance: {
        kind: "official_provider",
        source: "authorized-test",
        observed_at: "2026-07-29T03:34:00Z",
      },
      created_at: "2026-07-29T03:34:01Z",
      registration_valid_until: "2026-07-29T03:39:01Z",
    },
  }],
  status: "watching",
  reservation_policy: "notify_only",
  updated_at: "2026-07-29T00:00:00Z",
};

function apiWatchCandidate(): (typeof apiWatch.candidates)[number] {
  const candidate = apiWatch.candidates[0];
  if (candidate === undefined) throw new Error("Watch fixture candidate is missing");
  return candidate;
}

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
      trainNumber: "KTX 002",
      departureAt: "2026-08-08T10:00:00+09:00",
      arrivalAt: "2026-08-08T13:00:00+09:00",
      seatClass: "standard",
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
      paymentDeadline: null,
      createdAt: "2026-08-07T15:00:00Z",
      updatedAt: null,
      officialBookingUrl: "https://www.korail.com/ticket/search",
      reservationPolicy: "notify_only",
      payment_deadline: null,
      created_at: "2026-08-07T15:00:00Z",
      updated_at: null,
      official_booking_url: "https://www.korail.com/ticket/search",
      reservation_policy: "notify_only",
      candidates: [{
        id: "candidate-1",
        trainNumber: "KTX 001",
        departureAt: "2026-08-08T10:00:00+09:00",
        arrivalAt: null,
        seatClass: "standard",
        train_number: "KTX 001",
        departure_at: "2026-08-08T10:00:00+09:00",
        arrival_at: null,
        seat_class: "standard",
        priority: 1,
      }],
    });
    expect(mapped.paymentDeadline).toBe(mapped.payment_deadline);
    expect(mapped.createdAt).toBe(mapped.created_at);
    expect(mapped.updatedAt).toBe(mapped.updated_at);
    expect(mapped.officialBookingUrl).toBe(mapped.official_booking_url);
    expect(mapped.reservationPolicy).toBe(mapped.reservation_policy);
    const candidate = mapped.candidates[0];
    expect(candidate).toBeDefined();
    expect(candidate?.trainNumber).toBe(candidate?.train_number);
    expect(candidate?.departureAt).toBe(candidate?.departure_at);
    expect(candidate?.arrivalAt).toBe(candidate?.arrival_at);
    expect(candidate?.seatClass).toBe(candidate?.seat_class);
    const canonicalView: WatchReadModel = mapped;
    const compatibilityView: MappedWatch = mapped;
    expect(canonicalView.candidates).toBe(compatibilityView.candidates);
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
    const fetchMock = vi.fn<typeof fetch>()
      .mockImplementation(async () => jsonResponse(WATCH_DTO, 201));
    vi.stubGlobal("fetch", fetchMock);
    const evidenceId = "10000000-0000-4000-8000-000000000001";

    await createWatch({ candidates: [{ registration_evidence_id: evidenceId }] });
    await startWatch(WATCH_DTO.id);
    await cancelWatch(WATCH_DTO.id);

    expect(fetchMock.mock.calls.map(([, options]) => (
      new Headers(options?.headers).get("Idempotency-Key")
    ))).toEqual([
      `watch-create:${evidenceId}`,
      `watch-start:${WATCH_DTO.id}`,
      `watch-cancel:${WATCH_DTO.id}`,
    ]);
    expect(fetchMock.mock.calls.every(([, options]) => (
      options?.credentials === "include"
    ))).toBe(true);
  });

  it("annotates invalid create responses with the watch operation", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ id: "partial" }, 201)));

    const error = await createWatch({ provider: "korail" }).catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ operation: "watch.create" });
  });
  it("maps backend watch fields without inventing a status", () => {
    expect(mapWatch(apiWatch)).toMatchObject({
      provider: "KORAIL",
      train: "KTX 033",
      route: "서울 → 부산",
      departure: "13:18",
      arrival: "15:59",
      statusLabel: "감시 중",
      seatClass: "standard",
      seatClassLabel: "일반실",
      seatEvidenceLabel: "일반실 · 매진 · 공식 관측 12:34",
      lastCheckedAt: null,
      lastCheckedLabel: "최근 확인 기록 없음",
      origin: "서울",
      destination: "부산",
      travelDate: "2026-07-31",
      officialBookingUrl: null,
      seatFoundObservation: null,
      reservationPolicy: "notify_only",
    });
    expect(mapWatch({ ...apiWatch, last_checked_at: "2026-07-31T03:45:00Z" })).toMatchObject({
      lastCheckedAt: "2026-07-31T03:45:00Z",
      lastCheckedLabel: "최근 확인 12:45",
    });
    expect(mapWatch({ ...apiWatch, last_checked_at: "not-a-date" })).toMatchObject({
      lastCheckedAt: null,
      lastCheckedLabel: "최근 확인 기록 없음",
    });
  });

  it("uses the selected candidate's exact departure and arrival before the watch window", () => {
    expect(mapWatch({
      ...apiWatch,
      time_from: "12:00:00",
      time_to: "18:00:00",
    })).toMatchObject({
      departure: "13:18",
      arrival: "15:59",
    });
  });

  it("maps the attempted candidate and preserves every candidate context for result events", () => {
    const attempted = {
      ...apiWatch.candidates[0],
      id: "candidate-2",
      train_number: "KTX 085",
      departure_at: "2026-07-31T14:11:00+09:00",
      arrival_at: "2026-07-31T16:52:00+09:00",
      seat_class: "first",
      priority: 2,
      state: "reservation_attempted",
    };
    expect(mapWatch({
      ...apiWatch,
      status: "reserving",
      candidates: [apiWatch.candidates[0], attempted],
    })).toMatchObject({
      train: "KTX 085",
      departure: "14:11",
      arrival: "16:52",
      seatClassLabel: "특실",
      reservationCandidateContexts: {
        "candidate-2": {
          train: "KTX 085",
          seatClassLabel: "특실",
          date: "7월 31일 (금)",
          departure: "14:11",
          arrival: "16:52",
        },
      },
    });
  });

  it("restores only the supported persisted one-time reservation policy", () => {
    expect(mapWatch({
      ...apiWatch,
      reservation_policy: "reserve_once_before_payment",
    })).toMatchObject({ reservationPolicy: "reserve_once_before_payment" });
    expect(mapWatch({ ...apiWatch, reservation_policy: "pay_automatically" }))
      .toMatchObject({ reservationPolicy: "notify_only" });
  });

  it("maps the selected candidate's latest reservation attempt without inventing retry state", () => {
    const mapped = mapWatch({
      ...apiWatch,
      candidates: [{
        ...apiWatch.candidates[0],
        latest_reservation_attempt: {
          outcome: "not_available",
          started_at: "2026-08-02T13:04:43Z",
          finished_at: "2026-08-02T13:05:07Z",
          retryable: true,
          manual_check_required: false,
          retry_condition: "new_availability_episode",
        },
      }],
    });

    expect(mapped.latestReservationAttempt).toEqual({
      outcome: "not_available",
      startedAt: "2026-08-02T13:04:43Z",
      finishedAt: "2026-08-02T13:05:07Z",
      retryable: true,
      manualCheckRequired: false,
      retryCondition: "new_availability_episode",
      paymentHoldEndedAt: null,
    });
    expect(mapWatch({
      ...apiWatch,
      candidates: [{
        ...apiWatch.candidates[0],
        latest_reservation_attempt: {
          outcome: "not_available",
          started_at: "not-a-timestamp",
          retryable: true,
        },
      }],
    }).latestReservationAttempt).toBeNull();
    expect(mapWatch({
      ...apiWatch,
      candidates: [{
        ...apiWatch.candidates[0],
        latest_reservation_attempt: {
          outcome: "not_available",
          started_at: "2026-08-02T13:04:43Z",
          finished_at: "2026-08-02T13:05:07Z",
          retryable: true,
          manual_check_required: false,
          retry_condition: "unexpected_retry",
        },
      }],
    }).latestReservationAttempt).toBeNull();
  });

  it("maps a finalized missing payment hold only with the complete retry contract", () => {
    const attempt = {
      outcome: "payment_required",
      started_at: "2026-08-02T08:20:00Z",
      finished_at: "2026-08-02T08:22:00Z",
      retryable: true,
      manual_check_required: false,
      retry_condition: "new_availability_episode",
      confirmation_outcome: "not_found",
      post_deadline_reconciled_at: "2026-08-02T08:24:00Z",
      payment_hold_end_reason: "confirmed_payment_hold_no_longer_present",
    };
    const mapAttempt = (overrides: Record<string, unknown> = {}) => mapWatch({
      ...apiWatch,
      status: "seat_found",
      candidates: [{
        ...apiWatch.candidates[0],
        latest_reservation_attempt: { ...attempt, ...overrides },
      }],
    }).latestReservationAttempt;

    expect(mapAttempt()).toMatchObject({
      outcome: "payment_required",
      paymentHoldEndedAt: "2026-08-02T08:24:00Z",
      paymentHoldEndReason: "confirmed_payment_hold_no_longer_present",
    });
    expect(mapAttempt({ confirmation_outcome: "inconclusive" })).toMatchObject({
      paymentHoldEndedAt: null,
    });
    expect(mapAttempt({ post_deadline_reconciled_at: null })).toMatchObject({
      paymentHoldEndedAt: null,
    });
    expect(mapAttempt({ retryable: false })).toMatchObject({
      paymentHoldEndedAt: "2026-08-02T08:24:00Z",
    });
    expect(mapAttempt({ retry_condition: null })).toMatchObject({
      paymentHoldEndedAt: "2026-08-02T08:24:00Z",
    });
    expect(mapAttempt({ manual_check_required: true })).toMatchObject({
      paymentHoldEndedAt: null,
    });
    expect(mapAttempt({ post_deadline_reconciled_at: "2026-08-02T08:19:00Z" }))
      .toMatchObject({ paymentHoldEndedAt: null });
    expect(mapAttempt({ confirmation_outcome: "invented" })).toBeNull();
    expect(mapAttempt({ payment_hold_end_reason: null })).toMatchObject({
      paymentHoldEndedAt: null,
    });
    expect(mapAttempt({ post_deadline_reconciled_at: "2026-08-02 08:24:00" })).toBeNull();
  });

  it("labels a safe official scheduled watch without claiming an automatic check", () => {
    expect(mapWatch({ ...apiWatch, status: "scheduled", next_check_at: null })).toMatchObject({
      statusLabel: "대기 등록됨",
      activityLabel: "일반실 · 매진 · 공식 관측 12:34",
    });
  });

  it("maps seat-found observation context without inventing an exact availability status", () => {
    expect(mapWatch({
      ...apiWatch,
      status: "seat_found",
      last_checked_at: "2026-07-31T03:45:00Z",
      candidates: [{
        ...apiWatch.candidates[0],
        latest_observation: {
          status: "available",
          source: "authorized-test",
          observed_at: "2026-07-31T03:45:00Z",
          fresh_until: "2030-07-31T03:50:00Z",
          error_category: null,
        },
      }],
    })).toMatchObject({
      seatFoundObservation: {
        kind: "official_provider",
        observedAt: "2026-07-31T03:45:00Z",
        observedLabel: "최근 확인 12:45",
      },
    });

    expect(mapWatch({
      ...apiWatch,
      provider: "mock",
      status: "seat_found",
      candidates: [{
        ...apiWatch.candidates[0],
        latest_observation: {
          status: "available",
          source: "mock",
          observed_at: "2026-07-31T03:45:00Z",
          fresh_until: "2030-07-31T03:50:00Z",
          error_category: null,
        },
      }],
    })).toMatchObject({
      seatFoundObservation: {
        kind: "mock",
        observedAt: "2026-07-31T03:45:00Z",
        observedLabel: "최근 확인 12:45",
      },
    });
  });

  it("uses the latest sold-out observation and removes a stale booking action", () => {
    const mapped = mapWatch({
      ...apiWatch,
      status: "seat_found",
      candidates: [{
        ...apiWatch.candidates[0],
        registration_evidence: {
          ...apiWatchCandidate().registration_evidence,
          status: "available",
        },
        latest_observation: {
          status: "sold_out",
          source: "authorized-test",
          observed_at: "2026-07-31T03:46:00Z",
          fresh_until: "2030-07-31T03:51:00Z",
          error_category: null,
        },
      }],
    });

    expect(mapped).toMatchObject({
      seatEvidenceLabel: "일반실 · 매진 · 최근 관측 12:46",
      registrationEvidenceLabel: "일반실 · 예매 가능 · 공식 관측 12:34",
      seatFoundObservation: null,
    });
  });

  it("maps user-confirmed, not-observed, and legacy candidate evidence explicitly", () => {
    const baseCandidate = apiWatchCandidate();
    const userConfirmed = mapWatch({
      ...apiWatch,
      seat_class: "first",
      candidates: [{
        ...baseCandidate,
        seat_class: "first",
        registration_evidence: {
          ...baseCandidate.registration_evidence,
          status: "available",
          provenance: {
            kind: "user_confirmed_official_page",
            source: "official-page-user-confirmation",
            observed_at: "2026-07-29T03:34:00Z",
            fresh_until: "2026-07-29T03:39:00Z",
          },
        },
      }],
    });
    const notObserved = mapWatch({
      ...apiWatch,
      candidates: [{
        ...baseCandidate,
        registration_evidence: {
          ...baseCandidate.registration_evidence,
          status: "unknown",
          provenance: { kind: "not_observed", reason: "provider_access_restricted" },
        },
      }],
    });
    const legacy = mapWatch({
      ...apiWatch,
      candidates: [{ ...baseCandidate, registration_evidence: null }],
    });

    expect(userConfirmed.seatEvidenceLabel).toBe("특실 · 예매 가능 · 공식 페이지에서 직접 확인 12:34");
    expect(notObserved.seatEvidenceLabel).toBe("일반실 · 조회 제한");
    expect(legacy.seatEvidenceLabel).toBe("일반실 · 등록 근거 없음");
  });

  it("builds watch creation from selected timetable values, not the search window", () => {
    const selected = [mapTimetable({
      provider: "korail",
      train_number: "KTX 085",
      origin: "서울",
      destination: "부산",
      departure_at: "2026-08-01T14:11:00+09:00",
      arrival_at: "2026-08-01T16:52:00+09:00",
      official_booking_url: "https://www.letskorail.com",
      seat_classes: [{
        seat_class: "standard",
        status: "unknown",
        provenance: { kind: "not_observed", reason: "source_not_configured" },
        registration_evidence_id: "10000000-0000-4000-8000-000000000085",
        actions: [{ kind: "add_to_watch" }],
      }],
    })];
    expect(buildWatchCreatePayload({
      provider: "KORAIL",
      origin: "서울",
      origin_node_id: "N-SEOUL",
      destination: "부산",
      destination_node_id: "N-BUSAN",
      date: "2026-08-01",
      time: "10:00",
      seat: "일반실",
      passengers: "1",
    }, selected, ["channel-1"])).toEqual({
      provider: "korail",
      origin: "서울",
      origin_node_id: "N-SEOUL",
      destination: "부산",
      destination_node_id: "N-BUSAN",
      travel_date: "2026-08-01",
      time_from: "14:11:00",
      time_to: "16:52:00",
      seat_class: "standard",
      passenger_count: 1,
      reservation_policy: "notify_only",
      train_numbers: ["KTX 085"],
      candidates: [{
        train_number: "KTX 085",
        departure_at: "2026-08-01T14:11:00+09:00",
        arrival_at: "2026-08-01T16:52:00+09:00",
        seat_class: "standard",
        priority: 1,
        registration_evidence_id: "10000000-0000-4000-8000-000000000085",
      }],
      notification_channel_ids: ["channel-1"],
      mode: "official",
    });
  });

  it("sends the authenticated-account one-time reservation policy without a payment mode", () => {
    const selected = [mapTimetable({
      provider: "korail",
      train_number: "KTX 085",
      origin: "서울",
      destination: "부산",
      departure_at: "2026-08-01T14:11:00+09:00",
      arrival_at: "2026-08-01T16:52:00+09:00",
      seat_classes: [{
        seat_class: "standard",
        status: "sold_out",
        provenance: { kind: "official_provider", source: "authorized-test", observed_at: "2026-08-01T00:00:00Z" },
        registration_evidence_id: "10000000-0000-4000-8000-000000000086",
        actions: [{ kind: "add_to_watch" }],
      }],
    })];

    const payload = buildWatchCreatePayload({
      provider: "KORAIL",
      origin: "서울",
      origin_node_id: "N-SEOUL",
      destination: "부산",
      destination_node_id: "N-BUSAN",
      date: "2026-08-01",
      passengers: "1",
      seat: "일반실",
      reservationPolicy: "reserve_once_before_payment",
    }, selected);

    expect(payload.reservation_policy).toBe("reserve_once_before_payment");
    expect(JSON.stringify(payload)).not.toContain("pay_automatically");
  });

  it("rejects cross-provider trains in a watch payload", () => {
    expect(() => buildWatchCreatePayload({ provider: "KORAIL" }, [{ provider: "SRT" }])).toThrow(ApiError);
  });

  it("allows mock watch creation without official station node identities", () => {
    expect(buildWatchCreatePayload({
      provider: "MOCK",
      origin: "서울",
      destination: "부산",
      date: "2026-08-01",
      seat: "일반실",
      passengers: "1",
    }, [{
      provider: "MOCK",
      train_number: "MOCK-001",
      departure_at: "2026-08-01T10:00:00+09:00",
      arrival_at: "2026-08-01T12:00:00+09:00",
    }])).not.toHaveProperty("origin_node_id");
  });

  it("splits selected trains into one exact watch payload per train and seat class", () => {
    const form: WatchCreateForm = {
      providers: ["KORAIL", "SRT"],
      origin: "서울",
      origin_node_id: "N-SEOUL",
      destination: "부산",
      destination_node_id: "N-BUSAN",
      date: "2026-08-01",
      timeFrom: "09:00",
      timeTo: "18:00",
      selectedWeekdays: ["토요일"],
      seat: "상관없음",
      passengers: "2",
    };
    const selected = [
      { provider: "KORAIL", train_number: "KTX 001", departure_at: "2026-08-01T10:30:00+09:00", arrival_at: "2026-08-01T13:00:00+09:00", selected_seat_class: "standard", seat_classes: [{ seat_class: "standard", registration_evidence_id: "10000000-0000-4000-8000-000000000001" }] },
      { provider: "SRT", train_number: "SRT 200", departure_at: "2026-08-01T13:00:00+09:00", arrival_at: "2026-08-01T15:20:00+09:00", selected_seat_class: "standard", seat_classes: [{ seat_class: "standard", registration_evidence_id: "20000000-0000-4000-8000-000000000200" }] },
      { provider: "KORAIL", train_number: "KTX 003", departure_at: "2026-08-01T12:30:00+09:00", arrival_at: "2026-08-01T15:10:00+09:00", selected_seat_class: "standard", seat_classes: [{ seat_class: "standard", registration_evidence_id: "10000000-0000-4000-8000-000000000003" }] },
    ];

    const payloads = buildWatchCreatePayloads(form, selected, ["channel-1"]);
    expect(payloads).toHaveLength(3);
    expect(payloads.map((payload) => [payload.provider, payload.train_numbers, payload.seat_class])).toEqual([
      ["korail", ["KTX 001"], "standard"],
      ["korail", ["KTX 003"], "standard"],
      ["srt", ["SRT 200"], "standard"],
    ]);
    expect(payloads.every((payload) => (
      payload.candidates.length === 1 && payload.candidates[0]?.priority === 1
    ))).toBe(true);
  });

  it("splits one provider into independent seat-class watch payloads", () => {
    const form: WatchCreateForm = {
      providers: ["KORAIL"],
      origin: "서울",
      origin_node_id: "N-SEOUL",
      destination: "부산",
      destination_node_id: "N-BUSAN",
      date: "2026-08-01",
      timeFrom: "09:00",
      timeTo: "18:00",
      selectedWeekdays: ["토요일"],
      seat: "상관없음",
      passengers: "1",
    };
    const payloads = buildWatchCreatePayloads(form, [
      { provider: "KORAIL", train_number: "KTX 001", departure_at: "2026-08-01T10:30:00+09:00", arrival_at: "2026-08-01T13:00:00+09:00", selected_seat_class: "standard", seat_classes: [{ seat_class: "standard", registration_evidence_id: "10000000-0000-4000-8000-000000000001" }] },
      { provider: "KORAIL", train_number: "KTX 003", departure_at: "2026-08-01T12:30:00+09:00", arrival_at: "2026-08-01T15:10:00+09:00", selected_seat_class: "first", seat_classes: [{ seat_class: "first", registration_evidence_id: "10000000-0000-4000-8000-000000000003" }] },
    ]);

    expect(payloads).toHaveLength(2);
    expect(payloads.map((payload) => [payload.seat_class, payload.train_numbers])).toEqual([
      ["standard", ["KTX 001"]],
      ["first", ["KTX 003"]],
    ]);
  });

  it("rejects selected trains outside the form providers", () => {
    expect(() => buildWatchCreatePayloads(
      { providers: ["KORAIL"], date: "2026-08-01" },
      [{ provider: "SRT", train_number: "SRT 100" }],
    )).toThrow("선택한 운영사의 실제 열차를 다시 선택해 주세요");
  });

  it("keeps an empty API watch list empty", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse([])));
    await expect(fetchWatches()).resolves.toEqual([]);
  });

  it("sends credentials, idempotency and CSRF for watch creation", async () => {
    const fetchMock = vi.fn<typeof fetch>()
      .mockImplementation(async () => jsonResponse(apiWatch, 201));
    vi.stubGlobal("fetch", fetchMock);

    await createWatch({ provider: "korail", origin: "서울", destination: "부산" });
    const requestCall = fetchMock.mock.calls[0];
    expect(requestCall).toBeDefined();
    const [, options] = requestCall ?? [];
    const headers = new Headers(options?.headers);

    expect(options?.credentials).toBe("include");
    expect(headers.get("X-CSRF-Token")).toBe("csrf-token");
    expect(headers.get("Idempotency-Key")).toBeTruthy();
  });

  it("reuses evidence-bound create and watch-bound start keys after a lost response", async () => {
    const fetchMock = vi.fn<typeof fetch>()
      .mockImplementation(async () => jsonResponse(apiWatch, 201));
    vi.stubGlobal("fetch", fetchMock);
    const evidenceId = "10000000-0000-4000-8000-000000000001";
    const payload = {
      provider: "korail",
      origin: "서울",
      destination: "부산",
      candidates: [{ registration_evidence_id: evidenceId }],
    };

    await createWatch(payload);
    await createWatch(payload);
    await startWatch(apiWatch.id);
    await startWatch(apiWatch.id);

    const keys = fetchMock.mock.calls.map(([, options]) => (
      new Headers(options?.headers).get("Idempotency-Key")
    ));
    expect(keys).toEqual([
      `watch-create:${evidenceId}`,
      `watch-create:${evidenceId}`,
      `watch-start:${apiWatch.id}`,
      `watch-start:${apiWatch.id}`,
    ]);
  });

  it("updates an existing watch reservation policy through the patch contract", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({
      ...apiWatch,
      reservation_policy: "reserve_once_before_payment",
    }));
    vi.stubGlobal("fetch", fetchMock);

    const updated = await updateWatch(apiWatch.id, {
      reservation_policy: "reserve_once_before_payment",
    });

    expect(updated.reservationPolicy).toBe("reserve_once_before_payment");
    const requestCall = fetchMock.mock.calls[0];
    expect(requestCall).toBeDefined();
    const [url, options] = requestCall ?? [];
    expect(String(url)).toContain(`/watches/${apiWatch.id}`);
    expect(options?.method).toBe("PATCH");
    expect(JSON.parse(String(options?.body))).toEqual({
      reservation_policy: "reserve_once_before_payment",
    });
  });

});
