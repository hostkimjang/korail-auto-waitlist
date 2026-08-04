import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { TimetableRefreshSettings } from "../src/features/settings/TimetableRefreshSettings";
import type { UiPreferences } from "../src/api/uiPreferences";

const preferences: UiPreferences = {
  timetableRefreshIntervalSeconds: 5,
  seatObservationIntervalSeconds: 5,
  updatedAt: "2026-08-04T00:00:00Z",
};

describe("TimetableRefreshSettings", () => {
  it("shows one global seat observation interval with a five second default", () => {
    render(
      <TimetableRefreshSettings
        preferences={preferences}
        saving={false}
        onSave={vi.fn(async () => preferences)}
      />,
    );

    expect((screen.getByRole("spinbutton", { name: /화면 표시 갱신/ }) as HTMLInputElement).value).toBe("5");
    expect((screen.getByRole("spinbutton", { name: /좌석 관측 간격/ }) as HTMLInputElement).value).toBe("5");
    expect(screen.queryByText("균형 관측 간격")).toBeNull();
    expect(screen.queryByText("집중 관측 간격")).toBeNull();
    expect(screen.getByText(/활성 작업의 다음 관측부터 즉시 적용/)).toBeTruthy();
    expect(screen.getByText(/provider lease, 캐시, 백오프, 쿨다운/)).toBeTruthy();
  });

  it("accepts one second and saves both display and observation intervals", async () => {
    const user = userEvent.setup();
    const saved: UiPreferences = {
      ...preferences,
      timetableRefreshIntervalSeconds: 45,
      seatObservationIntervalSeconds: 1,
    };
    const onSave = vi.fn(async () => saved);
    render(
      <TimetableRefreshSettings preferences={preferences} saving={false} onSave={onSave} />,
    );

    const displayInput = screen.getByRole("spinbutton", { name: /화면 표시 갱신/ });
    const observationInput = screen.getByRole("spinbutton", { name: /좌석 관측 간격/ });
    await user.clear(observationInput);
    await user.type(observationInput, "0");
    await user.click(screen.getByRole("button", { name: "간격 저장" }));
    expect(screen.getByRole("alert").textContent).toContain("좌석 관측 간격은 1~600초 사이의 정수");
    expect(onSave).not.toHaveBeenCalled();

    await user.clear(displayInput);
    await user.type(displayInput, "45");
    await user.clear(observationInput);
    await user.type(observationInput, "1");
    await user.click(screen.getByRole("button", { name: "간격 저장" }));

    expect(onSave).toHaveBeenCalledWith({
      timetableRefreshIntervalSeconds: 45,
      seatObservationIntervalSeconds: 1,
    });
    expect(screen.getByRole("status").textContent).toContain("활성 작업의 다음 관측부터");
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
    expect(screen.getAllByRole("spinbutton").every((input) => input.hasAttribute("disabled"))).toBe(true);
    rerender(<TimetableRefreshSettings preferences={preferences} saving={false} onSave={onSave} />);

    const observationInput = screen.getByRole("spinbutton", { name: /좌석 관측 간격/ });
    await user.clear(observationInput);
    await user.type(observationInput, "120");
    await user.click(screen.getByRole("button", { name: "간격 저장" }));

    expect((await screen.findByRole("alert")).textContent).toContain("서버 저장 실패");
    expect((observationInput as HTMLInputElement).value).toBe("5");
    expect(onSave).toHaveBeenCalledTimes(1);
  });
});
