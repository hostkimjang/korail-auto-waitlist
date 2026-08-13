import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { TimetableRefreshSettings } from "../src/features/settings/TimetableRefreshSettings";
import type { UiPreferences } from "../src/api/uiPreferences";

const preferences: UiPreferences = {
  seatObservationIntervalSeconds: 5,
  updatedAt: "2026-08-04T00:00:00Z",
};

describe("TimetableRefreshSettings", () => {
  it("shows live display synchronization and one configurable seat observation interval", () => {
    render(
      <TimetableRefreshSettings
        preferences={preferences}
        saving={false}
        onSave={vi.fn(async () => preferences)}
      />,
    );

    expect(screen.getByText("화면 표시 갱신")).toBeTruthy();
    expect(screen.getByText("실시간")).toBeTruthy();
    expect(screen.getByText(/이벤트를 놓치면 내부 자동 조회/)).toBeTruthy();
    expect(screen.queryByRole("spinbutton", { name: /화면 표시 갱신/ })).toBeNull();
    expect((screen.getByRole("spinbutton", { name: /좌석 관측 간격/ }) as HTMLInputElement).value).toBe("5");
    expect(screen.queryByText("균형 관측 간격")).toBeNull();
    expect(screen.queryByText("집중 관측 간격")).toBeNull();
    expect(screen.getByText(/활성 작업의 다음 관측부터 즉시 적용/)).toBeTruthy();
    expect(screen.getByText(/provider lease, 캐시, 백오프, 쿨다운/)).toBeTruthy();
  });

  it("accepts one second for the seat observation interval", async () => {
    const user = userEvent.setup();
    const saved: UiPreferences = {
      ...preferences,
      seatObservationIntervalSeconds: 1,
    };
    const onSave = vi.fn(async () => saved);
    render(
      <TimetableRefreshSettings preferences={preferences} saving={false} onSave={onSave} />,
    );

    const observationInput = screen.getByRole("spinbutton", { name: /좌석 관측 간격/ });
    await user.clear(observationInput);
    await user.type(observationInput, "0");
    await user.click(screen.getByRole("button", { name: "관측 간격 저장" }));
    expect(screen.getByRole("alert").textContent).toContain("좌석 관측 간격은 1~600초 사이의 정수");
    expect(onSave).not.toHaveBeenCalled();

    await user.clear(observationInput);
    await user.type(observationInput, "1");
    await user.click(screen.getByRole("button", { name: "관측 간격 저장" }));

    expect(onSave).toHaveBeenCalledWith({
      seatObservationIntervalSeconds: 1,
    });
    expect(screen.getByRole("status").textContent).toContain("활성 작업의 다음 좌석 관측부터");
  });

  it("disables editing while saving and restores the previous values after a failure", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn(async () => {
      throw new Error("서버 저장 실패");
    });
    const { rerender } = render(
      <TimetableRefreshSettings preferences={preferences} saving={true} onSave={onSave} />,
    );

    expect((screen.getByRole("button", { name: "저장 중…" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getAllByRole("spinbutton")).toHaveLength(1);
    expect(screen.getByRole("spinbutton").hasAttribute("disabled")).toBe(true);
    rerender(<TimetableRefreshSettings preferences={preferences} saving={false} onSave={onSave} />);

    const observationInput = screen.getByRole("spinbutton", { name: /좌석 관측 간격/ });
    await user.clear(observationInput);
    await user.type(observationInput, "120");
    await user.click(screen.getByRole("button", { name: "관측 간격 저장" }));

    expect((await screen.findByRole("alert")).textContent).toContain("서버 저장 실패");
    expect((observationInput as HTMLInputElement).value).toBe("5");
    expect(onSave).toHaveBeenCalledTimes(1);
  });
});
