import { beforeEach, describe, expect, it, vi } from "vitest";
import { loginWithPassword, registerAdmin } from "../src/api/auth";
import { ApiError } from "../src/api/client";
import { subscribeToEvents } from "../src/api/events";
import {
  fetchTimetables,
  filterTimetables,
  mapTimetable,
} from "../src/api/timetables";
import {
  buildWatchCreatePayload,
  buildWatchCreatePayloads,
  createWatch,
  fetchWatches,
  mapWatch,
  startWatch,
  updateWatch,
} from "../src/api/watches";

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

function response(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("API integration contract", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    Object.defineProperty(document, "cookie", { writable: true, value: "rail_csrf=csrf-token" });
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
    const mapAttempt = (overrides = {}) => mapWatch({
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
          ...apiWatch.candidates[0].registration_evidence,
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
    const baseCandidate = apiWatch.candidates[0];
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

  it("maps the production timetable contract fields", () => {
    expect(mapTimetable({
      provider: "srt",
      train_number: "SRT 327",
      origin: "수서",
      destination: "부산",
      departure_at: "2026-08-01T14:30:00+09:00",
      arrival_at: "2026-08-01T16:58:00+09:00",
      official_booking_url: "https://etk.srail.kr",
    })).toMatchObject({
      provider: "SRT",
      name: "SRT 327",
      departure: "14:30",
      arrival: "16:58",
      duration: "2시간 28분",
      official_booking_url: "https://etk.srail.kr",
    });
  });

  it("queries production timetables with provider, route, date and time", async () => {
    const item = {
      provider: "srt",
      train_number: "SRT 327",
      origin: "수서",
      destination: "부산",
      departure_at: "2026-08-01T14:30:00+09:00",
      arrival_at: "2026-08-01T16:58:00+09:00",
      official_booking_url: "https://etk.srail.kr",
    };
    const fetchMock = vi.fn().mockResolvedValue(response([item]));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchTimetables({ provider: "SRT", origin: "수서", origin_node_id: "N-SUSEO", destination: "부산", destination_node_id: "N-BUSAN", date: "2026-08-01", time: "10:00" })).resolves.toMatchObject({
      trains: [{ provider: "SRT", train_number: "SRT 327" }],
      providerResults: { SRT: { status: "success", count: 1 } },
    });
    const [url, options] = fetchMock.mock.calls[0];
    const parsed = new URL(url, "https://railwait.local");
    expect(parsed.pathname).toBe("/api/v1/timetables");
    expect(parsed.searchParams.get("passenger_count")).toBe("1");
    expect(Object.fromEntries(parsed.searchParams)).toEqual({
      provider: "srt",
      origin: "수서",
      destination: "부산",
      departure_from: "2026-08-01T10:00:00+09:00",
      departure_to: "2026-08-01T23:59:00+09:00",
      passenger_count: "1",
      origin_node_id: "N-SUSEO",
      destination_node_id: "N-BUSAN",
    });
    expect(options.credentials).toBe("include");
  });

  it("forwards the selected passenger count to seat-aware timetable sources", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response([])));

    await fetchTimetables({
      provider: "SRT",
      origin: "수서",
      origin_node_id: "N-SUSEO",
      destination: "부산",
      destination_node_id: "N-BUSAN",
      date: "2026-08-01",
      time: "10:00",
      passengers: "3",
    });

    const [url] = fetch.mock.calls[0];
    expect(new URL(url, "https://railwait.local").searchParams.get("passenger_count")).toBe("3");
  });

  it("queries each selected provider once and merges, filters, sorts and deduplicates the result", async () => {
    const trains = {
      korail: [
        { provider: "korail", train_number: "KTX 002", origin: "서울", destination: "부산", departure_at: "2026-08-01T12:30:00+09:00", arrival_at: "2026-08-01T15:00:00+09:00", official_booking_url: "https://www.letskorail.com" },
        { provider: "korail", train_number: "KTX 001", origin: "서울", destination: "부산", departure_at: "2026-08-01T10:30:00+09:00", arrival_at: "2026-08-01T13:00:00+09:00", official_booking_url: "https://www.letskorail.com" },
      ],
      srt: [
        { provider: "srt", train_number: "SRT 100", origin: "서울", destination: "부산", departure_at: "2026-08-01T11:00:00+09:00", arrival_at: "2026-08-01T13:20:00+09:00", official_booking_url: "https://etk.srail.kr" },
        { provider: "srt", train_number: "SRT 100", origin: "서울", destination: "부산", departure_at: "2026-08-01T11:00:00+09:00", arrival_at: "2026-08-01T13:20:00+09:00", official_booking_url: "https://etk.srail.kr" },
        { provider: "srt", train_number: "SRT 200", origin: "서울", destination: "부산", departure_at: "2026-08-01T13:01:00+09:00", arrival_at: "2026-08-01T15:20:00+09:00", official_booking_url: "https://etk.srail.kr" },
      ],
    };
    const fetchMock = vi.fn(async (url) => {
      const provider = new URL(url, "https://railwait.local").searchParams.get("provider");
      return response(trains[provider]);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { trains: items, providerResults } = await fetchTimetables({
      providers: ["KORAIL", "SRT"],
      origin: "서울",
      origin_node_id: "N-SEOUL",
      destination: "부산",
      destination_node_id: "N-BUSAN",
      date: "2026-08-01",
      timeFrom: "10:00",
      timeTo: "13:00",
      selectedWeekdays: ["토"],
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.map(([url]) => new URL(url, "https://railwait.local").searchParams.get("provider"))).toEqual(["korail", "srt"]);
    expect(fetchMock.mock.calls.every(([url]) => new URL(url, "https://railwait.local").searchParams.get("departure_from") === "2026-08-01T10:00:00+09:00")).toBe(true);
    expect(fetchMock.mock.calls.every(([url]) => new URL(url, "https://railwait.local").searchParams.get("departure_to") === "2026-08-01T13:00:00+09:00")).toBe(true);
    expect(items.map((item) => item.id)).toEqual([
      "KORAIL:KTX 001:2026-08-01T10:30:00+09:00",
      "SRT:SRT 100:2026-08-01T11:00:00+09:00",
      "KORAIL:KTX 002:2026-08-01T12:30:00+09:00",
    ]);
    expect(items[1].official_booking_url).toBe("https://etk.srail.kr");
    expect(providerResults).toMatchObject({ KORAIL: { status: "success" }, SRT: { status: "success" } });
  });

  it("applies the same pure timetable filter to demo-shaped items", () => {
    const form = { providers: ["SRT", "KORAIL"], date: "2026-08-01", timeFrom: "11:00", timeTo: "12:00", selectedWeekdays: [6] };
    const items = [
      { provider: "KORAIL", train_number: "KTX 003", departure_at: "2026-08-01T12:01:00+09:00" },
      { provider: "SRT", train_number: "SRT 101", departure_at: "2026-08-01T11:30:00+09:00" },
      { provider: "SRT", train_number: "SRT 101", departure_at: "2026-08-01T11:30:00+09:00" },
      { provider: "KORAIL", train_number: "KTX 001", departure_at: "2026-08-01T11:00:00+09:00" },
    ];
    expect(filterTimetables(form, items).map((item) => item.train_number)).toEqual(["KTX 001", "SRT 101"]);
  });

  it("returns every successful provider item in the selected range without a frontend result cap", async () => {
    const makeItems = (provider, prefix, offset) => Array.from({ length: 23 }, (_, index) => {
      const minutes = 10 * 60 + offset + index * 10;
      const time = `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
      return {
        provider: provider.toLowerCase(),
        train_number: `${prefix} ${String(index + 1).padStart(3, "0")}`,
        origin: "서울",
        destination: "부산",
        departure_at: `2026-08-01T${time}:00+09:00`,
        arrival_at: `2026-08-01T15:00:00+09:00`,
      };
    });
    const results = {
      korail: makeItems("KORAIL", "KTX", 0),
      srt: makeItems("SRT", "SRT", 5),
    };
    vi.stubGlobal("fetch", vi.fn(async (url) => response(results[new URL(url, "https://railwait.local").searchParams.get("provider")])));

    const result = await fetchTimetables({
      providers: ["KORAIL", "SRT"],
      origin: "서울",
      origin_node_id: "N-SEOUL",
      destination: "부산",
      destination_node_id: "N-BUSAN",
      date: "2026-08-01",
      timeFrom: "10:00",
      timeTo: "14:00",
    });

    expect(result.trains).toHaveLength(46);
    expect(result.trains.map((train) => train.provider).filter((provider) => provider === "KORAIL")).toHaveLength(23);
    expect(result.trains.map((train) => train.provider).filter((provider) => provider === "SRT")).toHaveLength(23);
    expect(result.trains.map((train) => Date.parse(train.departure_at))).toEqual(
      [...result.trains].map((train) => Date.parse(train.departure_at)).sort((left, right) => left - right),
    );
  });

  it("rejects a travel date that does not match the selected weekdays before requesting", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchTimetables({
      providers: ["KORAIL"], origin: "서울", destination: "부산", date: "2026-08-01", timeFrom: "10:00", timeTo: "12:00", selectedWeekdays: ["MON"],
    })).rejects.toThrow("반복 날짜는 아직 자동 생성하지 않습니다");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects an incomplete station identity pair before requesting a timetable", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchTimetables({
      provider: "KORAIL",
      origin: "서울",
      origin_node_id: "N-SEOUL",
      destination: "부산",
      destination_node_id: null,
      date: "2026-08-01",
      timeFrom: "10:00",
      timeTo: "12:00",
    })).rejects.toThrow("식별자를 다시 선택");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects missing official station identities before requesting a timetable", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchTimetables({
      provider: "KORAIL",
      origin: "서울",
      destination: "부산",
      date: "2026-08-01",
      timeFrom: "10:00",
      timeTo: "12:00",
    })).rejects.toThrow("식별자를 다시 선택");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects an inverted timetable range", () => {
    expect(() => filterTimetables({ providers: ["KORAIL"], date: "2026-08-01", timeFrom: "13:00", timeTo: "10:00" }, []))
      .toThrow("조회 시간 범위를 올바르게 선택해 주세요");
  });

  it("preserves a production timetable 503 as a provider-scoped error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ detail: "TAGO service key is not configured" }, 503)));
    await expect(fetchTimetables({ provider: "KORAIL", origin: "서울", origin_node_id: "N-SEOUL", destination: "부산", destination_node_id: "N-BUSAN", date: "2026-08-01", time: "10:00" }))
      .resolves.toMatchObject({
        trains: [],
        providerResults: { KORAIL: { status: "error", httpStatus: 503, message: expect.stringContaining("TAGO service key is not configured") } },
      });
  });

  it("keeps one provider result when the other provider fails", async () => {
    const fetchMock = vi.fn(async (url) => {
      const provider = new URL(url, "https://railwait.local").searchParams.get("provider");
      if (provider === "srt") return response({ detail: "SRT unavailable" }, 503);
      return response([{ provider: "korail", train_number: "KTX 001", origin: "서울", destination: "부산", departure_at: "2026-08-01T10:30:00+09:00", arrival_at: "2026-08-01T13:00:00+09:00", official_booking_url: "https://www.korail.com/ticket/search" }]);
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchTimetables({ providers: ["KORAIL", "SRT"], origin: "서울", origin_node_id: "N-SEOUL", destination: "부산", destination_node_id: "N-BUSAN", date: "2026-08-01", timeFrom: "10:00", timeTo: "12:00" });
    expect(result.trains.map((train) => train.train_number)).toEqual(["KTX 001"]);
    expect(result.providerResults).toMatchObject({ KORAIL: { status: "success" }, SRT: { status: "error", httpStatus: 503 } });
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
    const form = {
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
    expect(payloads.every((payload) => payload.candidates.length === 1 && payload.candidates[0].priority === 1)).toBe(true);
  });

  it("splits one provider into independent seat-class watch payloads", () => {
    const form = {
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
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response([])));
    await expect(fetchWatches()).resolves.toEqual([]);
  });

  it("sends credentials, idempotency and CSRF for watch creation", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => response(apiWatch, 201));
    vi.stubGlobal("fetch", fetchMock);

    await createWatch({ provider: "korail", origin: "서울", destination: "부산" });
    const [, options] = fetchMock.mock.calls[0];

    expect(options.credentials).toBe("include");
    expect(options.headers.get("X-CSRF-Token")).toBe("csrf-token");
    expect(options.headers.get("Idempotency-Key")).toBeTruthy();
  });

  it("reuses evidence-bound create and watch-bound start keys after a lost response", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => response(apiWatch, 201));
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

    const keys = fetchMock.mock.calls.map(([, options]) => options.headers.get("Idempotency-Key"));
    expect(keys).toEqual([
      `watch-create:${evidenceId}`,
      `watch-create:${evidenceId}`,
      `watch-start:${apiWatch.id}`,
      `watch-start:${apiWatch.id}`,
    ]);
  });

  it("updates an existing watch reservation policy through the patch contract", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({
      ...apiWatch,
      reservation_policy: "reserve_once_before_payment",
    }));
    vi.stubGlobal("fetch", fetchMock);

    const updated = await updateWatch(apiWatch.id, {
      reservation_policy: "reserve_once_before_payment",
    });

    expect(updated.reservationPolicy).toBe("reserve_once_before_payment");
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain(`/watches/${apiWatch.id}`);
    expect(options.method).toBe("PATCH");
    expect(JSON.parse(options.body)).toEqual({
      reservation_policy: "reserve_once_before_payment",
    });
  });

  it("uses the dedicated administrator registration endpoint without a bootstrap header", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({ authenticated: true }, 201));
    vi.stubGlobal("fetch", fetchMock);

    await registerAdmin("admin", "x".repeat(16));
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain("/auth/register");
    expect(options.headers.has("X-Bootstrap-Token")).toBe(false);
    expect(JSON.parse(options.body)).toEqual({ username: "admin", password: expect.any(String) });
  });

  it("uses the administrator password login endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({ authenticated: true }));
    vi.stubGlobal("fetch", fetchMock);

    await loginWithPassword("admin", "x".repeat(16));
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain("/auth/login");
    expect(JSON.parse(options.body)).toEqual({ username: "admin", password: expect.any(String) });
  });

  it("ignores replayed SSE history and forwards only events created after subscription", () => {
    const originalEventSource = globalThis.EventSource;
    class FakeEventSource {
      static latest = null;

      constructor(url, options) {
        this.url = url;
        this.options = options;
        this.listeners = new Map();
        this.closed = false;
        FakeEventSource.latest = this;
      }

      addEventListener(type, listener) {
        this.listeners.set(type, listener);
      }

      emit(type, payload) {
        this.listeners.get(type)?.({ data: JSON.stringify(payload) });
      }

      close() {
        this.closed = true;
      }
    }
    globalThis.EventSource = FakeEventSource;
    const onEvent = vi.fn();
    const onError = vi.fn();

    try {
      const unsubscribe = subscribeToEvents(onEvent, onError, {
        subscribedAt: Date.parse("2026-07-31T00:00:00Z"),
      });
      const source = FakeEventSource.latest;
      source.emit("watch.created", { id: "old", created_at: "2026-07-30T23:59:59Z" });
      source.emit("watch.updated", { id: "current", created_at: "2026-07-31T00:00:00Z" });
      source.emit("watch.status_changed", { id: "future", created_at: "2026-07-31T00:00:01Z" });
      source.emit("watch.reservation_result", { id: "reservation", created_at: "2026-07-31T00:00:02Z" });

      expect(onEvent.mock.calls.map(([event]) => event.id)).toEqual(["current", "future", "reservation"]);
      expect(onError).not.toHaveBeenCalled();
      unsubscribe();
      expect(source.closed).toBe(true);
    } finally {
      globalThis.EventSource = originalEventSource;
    }
  });
});
