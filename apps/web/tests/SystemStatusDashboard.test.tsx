import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SystemStatusDashboard } from "../src/features/settings/SystemStatusDashboard";
import { mapOperationsSummary } from "../src/features/settings/operationsSummary";
import type { SeatStatusSource } from "../src/features/settings/seatStatusSources";
import { operationsPayload } from "./operationsSummary.test";

describe("SystemStatusDashboard", () => {
  it("shows a labelled loading state until the first summary arrives", () => {
    const loader = vi.fn(() => new Promise<never>(() => {}));
    render(<SystemStatusDashboard loader={loader} />);

    expect(screen.getByRole("status").textContent).toContain("불러오는 중");
    expect(screen.getByRole("button", { name: "새로고침" }).hasAttribute("disabled")).toBe(true);
  });

  it("renders trusted aggregates and treats a zero denominator as no record", async () => {
    const payload = operationsPayload();
    const loader = vi.fn().mockResolvedValue(mapOperationsSummary(payload));
    render(<SystemStatusDashboard loader={loader} />);

    expect(await screen.findByRole("heading", { name: "처리량" })).toBeTruthy();
    expect(screen.getByText("좌석 관측 오류율").nextElementSibling?.textContent).toBe("기록 없음");
    expect(screen.getByText("알림 최종 실패율").nextElementSibling?.textContent).toBe("10.0%");
    expect(screen.getByText(payload.seat_observation_error_rate.definition)).toBeTruthy();
    expect(screen.getByText("알림 전달 · 전달됨")).toBeTruthy();
    expect(screen.getByText(
      /KORAIL · 예약 처리 · 좌석 확보 실패 · 123 · .*8월 15일.*토.*13:57.*일반실/,
    )).toBeTruthy();
    expect(screen.getByText("예매 시점에 요청 좌석을 확보하지 못했습니다.")).toBeTruthy();
    expect(screen.getByText("공식 내역에서 결제 완료를 확인했습니다.")).toBeTruthy();
    expect(screen.getByText(/KORAIL · 좌석 조회 · 관측 오류 · 382/)).toBeTruthy();
    expect(screen.getByText("오류 · 시간 초과")).toBeTruthy();
    expect(screen.getByText("운영사 로그인 확인이 필요해 예매를 진행하지 못했습니다.")).toBeTruthy();
    expect(screen.getByText(
      /공식 내역에서 이번 예매 시도와 정확히 일치하는 항목을 하나로 구분하지 못했습니다\. · 철도사 운행 지연 안내에 사용자 동의가 필요합니다\./,
    )).toBeTruthy();
    expect(screen.getByText("활동·오류 최대 20개 · 반복 정상 관측 제외")).toBeTruthy();
    expect(screen.getAllByRole("list").length).toBeGreaterThan(0);
    expect(screen.getAllByLabelText(/^기록 시각 /).length).toBeGreaterThan(0);
    expect(screen.queryByText(/https?:\/\/|서울|payload|token|watch-|candidate-/i)).toBeNull();
  });

  it("labels standing-only seat observations in the operations summary", async () => {
    const payload = operationsPayload();
    const standingOnlyEntry = {
      ...payload.recent_entries[3],
      status: "standing_only",
      error_category: null,
    };
    const loader = vi.fn().mockResolvedValue(mapOperationsSummary({
      ...payload,
      recent_entries: [standingOnlyEntry],
    }));
    render(<SystemStatusDashboard loader={loader} />);

    expect(await screen.findByText(/입석만 가능 관측/)).toBeTruthy();
  });

  it("shows every safe official-confirmation diagnostic in reservation attempt logs", async () => {
    const diagnostics = [
      [
        "official_read_unavailable",
        "철도사 공식 내역을 불러오거나 응답을 확인하지 못했습니다.",
      ],
      [
        "credential_context_mismatch",
        "예매 시도와 공식 확인의 계정 상태가 달라 결과를 연결하지 못했습니다.",
      ],
      [
        "official_record_ambiguous",
        "공식 내역에서 이번 예매 시도와 정확히 일치하는 항목을 하나로 구분하지 못했습니다.",
      ],
      [
        "official_evidence_insufficient",
        "공식 내역은 확인했지만 예약 상태를 확정할 정보가 충분하지 않습니다.",
      ],
      [
        "unspecified",
        "공식 예약 내역 확인으로 결과를 확정하지 못했습니다.",
      ],
    ] as const;
    const payload = {
      ...operationsPayload(),
      recent_entries: diagnostics.map(([confirmation_diagnostic_code], index) => ({
        occurred_at: `2026-07-29T03:${String(29 - index).padStart(2, "0")}:00Z`,
        kind: "reservation_attempt",
        level: "warning",
        status: "unknown",
        error_category: null,
        provider: "korail",
        train_number: String(100 + index),
        departure_at: "2026-08-15T04:57:00Z",
        seat_class: "standard",
        reason_code: null,
        confirmation_diagnostic_code,
      })),
    };
    const loader = vi.fn().mockResolvedValue(mapOperationsSummary(payload));
    render(<SystemStatusDashboard loader={loader} />);

    await screen.findByRole("heading", { name: "처리량" });
    for (const [, label] of diagnostics) expect(screen.getByText(label)).toBeTruthy();
    expect(screen.queryByText(/결제 (실패|취소|완료)/)).toBeNull();
  });

  it("keeps the last safe summary when a manual refresh fails", async () => {
    const user = userEvent.setup();
    const loader = vi.fn()
      .mockResolvedValueOnce(mapOperationsSummary(operationsPayload()))
      .mockRejectedValueOnce(new Error("일시적인 연결 오류"));
    render(<SystemStatusDashboard loader={loader} />);
    await screen.findByRole("heading", { name: "처리량" });

    await user.click(screen.getByRole("button", { name: "새로고침" }));

    expect((await screen.findByRole("alert")).textContent).toContain("마지막으로 확인한 집계를 유지합니다");
    expect(screen.getByRole("heading", { name: "처리량" })).toBeTruthy();
  });

  it("keeps seat-source cooldown separate from worker provider circuits", async () => {
    const loader = vi.fn().mockResolvedValue(mapOperationsSummary(operationsPayload()));
    const sources: SeatStatusSource[] = [
      {
        provider: "korail",
        source: "korail_browser",
        state: "cooldown",
        cause: "provider_access_restricted",
        retryAfterSeconds: 90,
      },
      {
        provider: "srt",
        source: "srt_live",
        state: "ready",
        cause: null,
        retryAfterSeconds: null,
      },
    ];
    const seatStatusSourcesLoader = vi.fn().mockResolvedValue(sources);
    render(
      <SystemStatusDashboard
        loader={loader}
        seatStatusSourcesLoader={seatStatusSourcesLoader}
      />,
    );

    expect(await screen.findByRole("heading", { name: "좌석 조회 제공원 상태" })).toBeTruthy();
    expect(screen.getByText("KORAIL 브라우저 좌석 조회")).toBeTruthy();
    expect(screen.getByText("공식 조회 제한 · 남은 1분 30초")).toBeTruthy();
    expect(screen.getByText("조회 대기 중")).toBeTruthy();
    expect(screen.getByText("별도 조회 대기 기준")).toBeTruthy();
    expect(screen.getByText("SRT 실시간 좌석 조회")).toBeTruthy();
    expect(screen.getByText("현재 제한 기록 없음")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "운영사 요청 상태" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "KORAIL 브라우저 연결" })).toBeNull();
  });
});
