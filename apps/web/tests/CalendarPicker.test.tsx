import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CalendarPicker } from "../src/features/new-wait/CalendarPicker";

const fixedNow = new Date("2026-07-28T16:00:00.000Z").getTime();

afterEach(() => {
  vi.restoreAllMocks();
});

describe("CalendarPicker", () => {
  it("opens a labelled modal with a 42-day custom calendar and disables past dates", async () => {
    vi.spyOn(Date, "now").mockReturnValue(fixedNow);
    const user = userEvent.setup();
    render(
      <CalendarPicker
        value="2026-07-30"
        label="출발일"
        dialogLabel="시간표 출발일 선택"
        onChange={() => {}}
      />,
    );

    const trigger = screen.getByRole("button", { name: "출발일: 7월 30일 (목)" });
    expect(trigger.getAttribute("aria-haspopup")).toBe("dialog");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");

    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "시간표 출발일 선택" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(within(dialog).getByLabelText("7월 달력").querySelectorAll("button")).toHaveLength(42);
    const pastDate = within(dialog).getByRole("button", { name: "7월 28일 (화)" });
    const today = within(dialog).getByRole("button", { name: "7월 29일 (수)" });
    expect(pastDate).toBeInstanceOf(HTMLButtonElement);
    expect(today).toBeInstanceOf(HTMLButtonElement);
    expect("disabled" in pastDate && pastDate.disabled).toBe(true);
    expect("disabled" in today && today.disabled).toBe(false);
    expect(within(dialog).getByRole("button", { name: "7월 30일 (목)" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("selects calendar and quick dates using the Asia/Seoul date boundary", async () => {
    vi.spyOn(Date, "now").mockReturnValue(fixedNow);
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<CalendarPicker value="2026-07-30" onChange={onChange} />);

    const trigger = screen.getByRole("button", { name: "가는 날: 7월 30일 (목)" });
    await user.click(trigger);
    await user.click(screen.getByRole("button", { name: "7월 31일 (금)" }));
    expect(onChange).toHaveBeenLastCalledWith("2026-07-31");

    await user.click(trigger);
    await user.click(screen.getByRole("button", { name: "내일" }));
    expect(onChange).toHaveBeenLastCalledWith("2026-07-30");

    await user.click(trigger);
    await user.click(screen.getByRole("button", { name: "이번 주말" }));
    expect(onChange).toHaveBeenLastCalledWith("2026-08-01");
  });

  it("traps tab focus, closes with Escape, and restores trigger focus", async () => {
    vi.spyOn(Date, "now").mockReturnValue(fixedNow);
    const user = userEvent.setup();
    render(<CalendarPicker value="2026-07-30" onChange={() => {}} />);

    const trigger = screen.getByRole("button", { name: "가는 날: 7월 30일 (목)" });
    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "가는 날 선택" });
    const first = within(dialog).getByRole("button", { name: "이전 달" });
    const enabledButtons = within(dialog).getAllByRole("button").filter(
      (button): button is HTMLButtonElement => button instanceof HTMLButtonElement && !button.disabled,
    );
    const last = enabledButtons[enabledButtons.length - 1];
    expect(last).toBeDefined();

    first.focus();
    await user.tab({ shift: true });
    expect(document.activeElement).toBe(last);

    await user.tab();
    expect(document.activeElement).toBe(first);

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(trigger));
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });
});
