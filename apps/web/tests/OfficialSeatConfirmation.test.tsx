import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { OfficialSeatConfirmation } from "../src/features/new-wait/OfficialSeatConfirmation";
import {
  mapOfficialSeatConfirmationResponse,
  saveOfficialSeatConfirmation,
} from "../src/features/new-wait/officialSeatConfirmationApi";

const train = {
  id: "KORAIL:26:2026-07-30T12:00:00+09:00",
  provider: "KORAIL" as const,
  name: "KTX 26",
  train_number: "26",
  origin: "대전",
  destination: "서울",
  departure: "12:00",
  arrival: "13:04",
  departure_at: "2026-07-30T12:00:00+09:00",
};

function successfulResponse() {
  const now = Date.now();
  const observedAt = new Date(now - 60_000).toISOString();
  const freshUntil = new Date(now + 4 * 60_000).toISOString();
  return {
    provider: "korail",
    origin_node_id: "0010",
    destination_node_id: "0001",
    train_number: "26",
    departure_at: "2026-07-30T03:00:00Z",
    passenger_count: 1,
    seat_classes: [
      { id: "confirmation-standard", seat_class: "standard", status: "sold_out" },
      { id: "confirmation-first", seat_class: "first", status: "available" },
    ],
    source: "official-page-user-confirmation",
    provenance_kind: "user_confirmed_official_page",
    observed_at: observedAt,
    fresh_until: freshUntil,
    created_count: 2,
    replayed: false,
  };
}

function jsonResponse(body: unknown, status = 201): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("official page seat confirmation boundary", () => {
  it("maps only fixed, aware user-confirmed provenance", () => {
    const payload = successfulResponse();
    expect(mapOfficialSeatConfirmationResponse(payload)).toMatchObject({
      provider: "KORAIL",
      source: "official-page-user-confirmation",
      provenanceKind: "user_confirmed_official_page",
      observedAt: payload.observed_at,
      freshUntil: payload.fresh_until,
      freshnessTtlMs: 5 * 60_000,
      seatClasses: [
        { seatClass: "standard", status: "sold_out" },
        { seatClass: "first", status: "available" },
      ],
    });
    expect(() => mapOfficialSeatConfirmationResponse({
      ...successfulResponse(),
      source: "arbitrary-client-source",
    })).toThrow(/근거를 검증하지 못했습니다/);
    expect(() => mapOfficialSeatConfirmationResponse({
      ...successfulResponse(),
      observed_at: "2026-07-29T15:30:00",
    })).toThrow(/근거를 검증하지 못했습니다/);
    expect(() => mapOfficialSeatConfirmationResponse({
      ...successfulResponse(),
      observed_at: "2020-01-01T00:00:00Z",
      fresh_until: "2020-01-01T00:00:00Z",
    })).toThrow(/근거를 검증하지 못했습니다/);
    expect(() => mapOfficialSeatConfirmationResponse({
      ...successfulResponse(),
      observed_at: "2020-01-01T00:00:00Z",
      fresh_until: "2020-01-01T00:05:01Z",
    })).toThrow(/근거를 검증하지 못했습니다/);
  });

  it("uses server TTL at receipt instead of comparing with the browser wall clock", () => {
    const monotonic = vi.spyOn(performance, "now").mockReturnValue(1_234);
    const wallClock = vi.spyOn(Date, "now").mockReturnValue(Date.parse("2099-01-01T00:00:00Z"));
    const result = mapOfficialSeatConfirmationResponse({
      ...successfulResponse(),
      observed_at: "2020-01-01T00:00:00Z",
      fresh_until: "2020-01-01T00:05:00Z",
    });

    expect(result.receivedAtMonotonicMs).toBe(1_234);
    expect(result.freshnessTtlMs).toBe(5 * 60_000);
    monotonic.mockRestore();
    wallClock.mockRestore();
  });

  it("sends a normalized atomic batch with CSRF and idempotency", async () => {
    Object.defineProperty(document, "cookie", { writable: true, value: "rail_csrf=csrf-token" });
    const captured: { url: string; init: RequestInit | null } = { url: "", init: null };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      captured.url = String(input);
      captured.init = init ?? null;
      return jsonResponse(successfulResponse());
    });
    vi.stubGlobal("fetch", fetchMock);

    await saveOfficialSeatConfirmation({
      provider: "KORAIL",
      origin_node_id: "0010",
      destination_node_id: "0001",
      train_number: "26",
      departure_at: "2026-07-30T12:00:00+09:00",
      passenger_count: 1,
      seat_classes: [
        { seat_class: "standard", status: "sold_out" },
        { seat_class: "first", status: "available" },
      ],
    }, "idempotency-key");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(captured.url).toBe("/api/v1/seat-observations/official-page-confirmations");
    if (!captured.init) throw new Error("request init was not captured");
    expect(captured.init.method).toBe("POST");
    expect(captured.init.credentials).toBe("include");
    const headers = new Headers(captured.init.headers);
    expect(headers.get("X-CSRF-Token")).toBe("csrf-token");
    expect(headers.get("Idempotency-Key")).toBe("idempotency-key");
    expect(JSON.parse(String(captured.init.body))).toEqual({
      provider: "korail",
      origin_node_id: "0010",
      destination_node_id: "0001",
      train_number: "26",
      departure_at: "2026-07-30T12:00:00+09:00",
      passenger_count: 1,
      seat_classes: [
        { seat_class: "standard", status: "sold_out" },
        { seat_class: "first", status: "available" },
      ],
    });
  });
});

describe("OfficialSeatConfirmation", () => {
  it("requires a visible official-page choice, saves both classes atomically, and refreshes", async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(successfulResponse())));
    render(<div className="app-shell"><OfficialSeatConfirmation
      train={train}
      originNodeId="0010"
      destinationNodeId="0001"
      passengerCount={1}
      officialUrl="https://www.korail.com/ticket/search"
      onSaved={onSaved}
    /></div>);

    const trigger = screen.getByRole("button", { name: "KTX 26 공식 페이지에서 확인한 좌석 상태 입력" });
    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "공식 좌석 상태 입력" });
    expect(within(dialog).getByRole("button", { name: "확인 결과 저장" }).hasAttribute("disabled")).toBe(true);
    expect(within(dialog).getByRole("link", { name: /공식 페이지 열기/ }).getAttribute("href")).toBe("https://www.korail.com/ticket/search");

    await user.selectOptions(within(dialog).getByRole("combobox", { name: "일반실 공식 페이지 확인 결과" }), "sold_out");
    await user.selectOptions(within(dialog).getByRole("combobox", { name: "특실 공식 페이지 확인 결과" }), "available");
    await user.click(within(dialog).getByRole("button", { name: "확인 결과 저장" }));

    expect((await within(dialog).findByRole("status")).textContent).toContain("공식 페이지에서 확인한 좌석 상태");
    expect(onSaved).toHaveBeenCalledTimes(1);
    expect(within(dialog).getByRole("button", { name: "저장 완료" }).hasAttribute("disabled")).toBe(true);
  });

  it("traps focus, closes on Escape, and returns focus to the train action", async () => {
    const user = userEvent.setup();
    render(<div className="app-shell"><OfficialSeatConfirmation
      train={train}
      originNodeId="0010"
      destinationNodeId="0001"
      passengerCount={1}
      officialUrl="https://www.korail.com/ticket/search"
      onSaved={vi.fn()}
    /></div>);
    const trigger = screen.getByRole("button", { name: "KTX 26 공식 페이지에서 확인한 좌석 상태 입력" });
    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "공식 좌석 상태 입력" });
    expect(document.documentElement.style.overflow).toBe("hidden");
    expect(document.body.style.position).toBe("fixed");
    const closeButton = within(dialog).getByRole("button", { name: "공식 좌석 상태 입력 닫기" });
    await waitFor(() => expect(document.activeElement).toBe(closeButton));
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "공식 좌석 상태 입력" })).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(trigger));
    expect(document.documentElement.style.overflow).toBe("");
    expect(document.body.style.position).toBe("");
  });
});
