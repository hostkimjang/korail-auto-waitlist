import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { StepThreeTimeRange } from "../src/features/new-wait/StepThreeTimeRange";

describe("StepThreeTimeRange", () => {
  it("offers the service-date midnight boundary without sending an inverted 00:00 end", () => {
    render(
      <StepThreeTimeRange
        appliedStart="12:00"
        appliedEnd="18:00"
        busy={false}
        onApply={() => {}}
      />,
    );

    const start = screen.getByRole("combobox", { name: "재조회 시작 시간" });
    const end = screen.getByRole("combobox", { name: "재조회 종료 시간" });

    expect(start.querySelector('option[value="00:00"]')?.textContent).toBe("00:00");
    expect(end.querySelector('option[value="00:00"]')).toBeNull();
    expect(end.querySelector('option[value="23:59"]')?.textContent).toBe("다음 날 00:00");
  });

  it("applies the evening preset through the same-day 23:59 API boundary", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    render(
      <StepThreeTimeRange
        appliedStart="12:00"
        appliedEnd="18:00"
        busy={false}
        onApply={onApply}
      />,
    );

    await user.click(screen.getByRole("button", {
      name: "저녁 18:00부터 익일 00:00까지",
    }));
    await user.click(screen.getByRole("button", { name: "적용·재조회" }));

    expect(onApply).toHaveBeenCalledOnce();
    expect(onApply).toHaveBeenCalledWith("18:00", "23:59");
  });

  it("applies the dawn preset from the start of the service date", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    render(
      <StepThreeTimeRange
        appliedStart="12:00"
        appliedEnd="18:00"
        busy={false}
        onApply={onApply}
      />,
    );

    await user.click(screen.getByRole("button", {
      name: "새벽 00:00부터 09:00까지",
    }));
    await user.click(screen.getByRole("button", { name: "적용·재조회" }));

    expect(onApply).toHaveBeenCalledOnce();
    expect(onApply).toHaveBeenCalledWith("00:00", "09:00");
  });

  it("resets an unapplied draft when the applied range changes", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    const { rerender } = render(
      <StepThreeTimeRange
        appliedStart="12:00"
        appliedEnd="18:00"
        busy={false}
        onApply={onApply}
      />,
    );
    await user.selectOptions(screen.getByRole("combobox", { name: "재조회 시작 시간" }), "13:00");

    rerender(
      <StepThreeTimeRange
        appliedStart="18:00"
        appliedEnd="23:59"
        busy={false}
        onApply={onApply}
      />,
    );

    expect(screen.getByRole<HTMLSelectElement>("combobox", { name: "재조회 시작 시간" }).value)
      .toBe("18:00");
    expect(screen.getByRole<HTMLSelectElement>("combobox", { name: "재조회 종료 시간" }).value)
      .toBe("23:59");
    expect(screen.getByRole("status").textContent).toContain("18:00–00:00 조회 완료");
  });
});
