import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { StepThreeDateSelector } from "../src/features/new-wait/StepThreeDateSelector";

describe("StepThreeDateSelector", () => {
  it("opens the actual departure-date calendar instead of offering weekday requery buttons", async () => {
    const user = userEvent.setup();
    render(
      <StepThreeDateSelector
        value="2099-07-29"
        appliedDateLabel="7월 29일 (수)"
        busy={false}
        onChange={() => {}}
      />,
    );

    const group = screen.getByRole("group", { name: /출발일 변경/ });
    expect(within(group).queryByRole("button", { name: "월" })).toBeNull();
    await user.click(within(group).getByRole("button", { name: /출발일:/ }));
    expect(screen.getByRole("dialog", { name: "시간표 출발일 선택" })).toBeTruthy();
    expect(within(group).getByRole("status").textContent).toBe("7월 29일 (수) 출발일 적용됨");
  });

  it("passes a calendar date change through and reports loading", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <StepThreeDateSelector
        value="2099-07-29"
        appliedDateLabel="7월 29일 (수)"
        busy
        onChange={onChange}
      />,
    );

    await user.click(screen.getByRole("button", { name: /출발일:/ }));
    const calendar = screen.getByRole("dialog", { name: "시간표 출발일 선택" });
    await user.click(within(calendar).getByRole("button", { name: "7월 30일 (목)" }));

    expect(onChange).toHaveBeenCalledWith("2099-07-30");
    expect(screen.getByRole("status").textContent).toBe("7월 29일 (수) 시간표 조회 중");
  });
});
