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
    expect(screen.queryByText(/https?:\/\/|KTX|서울|payload|token/i)).toBeNull();
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
    expect(screen.getByText("SRT 실시간 좌석 조회")).toBeTruthy();
    expect(screen.getByText("현재 제한 기록 없음")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "운영사 요청 상태" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "KORAIL 브라우저 연결" })).toBeNull();
  });
});
