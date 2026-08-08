import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { pushNotifications, initialNotificationCenterState } from "../src/features/app/notificationCenter";
import { AppNotificationCenter } from "../src/features/app/AppNotificationCenter";
import { CalendarPicker } from "../src/features/new-wait/CalendarPicker";

function centerState() {
  return pushNotifications(initialNotificationCenterState, [
    {
      title: "첫 번째 좌석",
      subjectKey: "watch:one",
      revisionKey: "watch:one:1",
      kind: "seat_found",
      meta: "KORAIL · KTX 001 · 일반실",
      occurredAt: "2026-08-03T12:16:34Z",
    },
    {
      title: "두 번째 좌석",
      subjectKey: "watch:two",
      revisionKey: "watch:two:1",
      kind: "seat_found",
      meta: "SRT · SRT 302 · 특실",
    },
    {
      title: "예매 진행",
      subjectKey: "watch:three",
      revisionKey: "watch:three:1",
      kind: "reserving",
      occurredAt: "2026-08-03T12:09:45Z",
      startedAt: "2026-08-03T12:09:45Z",
      durationMs: 3_250,
      steps: [{ label: "예매 요청 완료", state: "completed" }],
    },
    {
      title: "일반 안내",
      subjectKey: "generic:timed",
      revisionKey: "generic:timed:1",
      kind: "generic",
      autoCloseMs: 30_000,
    },
  ]);
}

describe("AppNotificationCenter", () => {
  it("renders one region, groups simultaneous events, and does not steal focus", async () => {
    const onDismissGroup = vi.fn();
    const anchor = document.createElement("button");
    document.body.append(anchor);
    anchor.focus();

    render(
      <AppNotificationCenter
        state={centerState()}
        onDismiss={vi.fn()}
        onDismissGroup={onDismissGroup}
        onDismissTimed={vi.fn()}
      />,
    );

    const center = screen.getByRole("region", { name: "실시간 알림" });
    expect(screen.getAllByRole("region", { name: "실시간 알림" })).toHaveLength(1);
    expect(document.activeElement).toBe(anchor);
    expect(await within(center).findByText("첫 번째 좌석", {
      selector: ".notification-center-peek strong",
    })).toBeTruthy();
    fireEvent.click(within(center).getByRole("button", { name: "자세히" }));
    const timestamp = within(center).getByText("21:16:34");
    expect(timestamp.tagName).toBe("TIME");
    expect(timestamp.getAttribute("datetime")).toBe("2026-08-03T12:16:34Z");
    expect(timestamp.getAttribute("aria-label")).toBe("알림 발생 시각 21:16:34");
    expect(within(center).getByLabelText("예매 작업 시간").textContent)
      .toContain("시작 21:09:45소요 3.3초");
    expect(within(center).getByText("좌석 발견").nextElementSibling?.textContent).toBe("2건");
    fireEvent.click(within(center).getByRole("button", { name: "추가 1건 보기" }));
    expect(within(center).getByText("두 번째 좌석")).toBeTruthy();
    fireEvent.click(within(center).getByRole("button", { name: "좌석 발견 2건 모두 닫기" }));
    expect(onDismissGroup).toHaveBeenCalledWith("seat_found");
    anchor.remove();
  });

  it("shows a brief foreground peek, keeps the notice, and preserves timed dismissal", () => {
    vi.useFakeTimers();
    const onDismiss = vi.fn();
    render(
      <AppNotificationCenter
        state={centerState()}
        onDismiss={onDismiss}
        onDismissGroup={vi.fn()}
        onDismissTimed={vi.fn()}
      />,
    );

    const announcement = screen.getByRole("status");
    act(() => vi.advanceTimersByTime(0));
    expect(document.documentElement.style.overflow).toBe("");
    expect(document.body.style.position).toBe("");
    expect(announcement.textContent).toContain("좌석 발견 2건");
    expect(announcement.textContent).toContain("예매 진행 1건");
    expect(screen.getByRole("button", { name: "실시간 알림 펼치기" })).toBeTruthy();
    expect(document.querySelector(".notification-center-peek")).not.toBeNull();
    act(() => vi.advanceTimersByTime(8_000));
    expect(document.querySelector(".notification-center-peek")).toBeNull();
    expect(screen.getByText("실시간 알림").nextElementSibling?.textContent).toBe("4건");
    fireEvent.click(screen.getByRole("button", { name: "실시간 알림 펼치기" }));
    fireEvent.click(screen.getByRole("button", { name: "실시간 알림 접기" }));
    expect(
      screen.getByText("첫 번째 좌석").closest(".notification-center-body")?.hasAttribute("hidden"),
    ).toBe(true);
    expect(document.documentElement.style.overflow).toBe("");
    expect(document.body.style.position).toBe("");
    act(() => vi.advanceTimersByTime(22_000));
    expect(onDismiss).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });

  it("hides only the foreground preview without dismissing its persistent notice", async () => {
    const onDismiss = vi.fn();
    render(
      <AppNotificationCenter
        state={centerState()}
        onDismiss={onDismiss}
        onDismissGroup={vi.fn()}
        onDismissTimed={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole("button", {
      name: "첫 번째 좌석 알림 미리보기 숨기기",
    }));

    expect(document.querySelector(".notification-center-peek")).toBeNull();
    expect(onDismiss).not.toHaveBeenCalled();
    expect(screen.getByText("실시간 알림").nextElementSibling?.textContent).toBe("4건");
  });

  it("keeps expanded notifications non-blocking while a real modal owns the scroll lock", () => {
    render(
      <>
        <AppNotificationCenter
          state={centerState()}
          onDismiss={vi.fn()}
          onDismissGroup={vi.fn()}
          onDismissTimed={vi.fn()}
        />
        <CalendarPicker value="2026-08-08" onChange={vi.fn()} />
      </>,
    );

    expect(screen.getByRole("button", { name: "실시간 알림 펼치기" })).toBeTruthy();
    expect(document.documentElement.style.overflow).toBe("");
    expect(document.body.style.position).toBe("");

    fireEvent.click(screen.getByRole("button", { name: "실시간 알림 펼치기" }));

    fireEvent.click(screen.getByRole("button", { name: /가는 날:/ }));
    expect(screen.getByRole("dialog", { name: "가는 날 선택" })).toBeTruthy();
    expect(document.documentElement.style.overflow).toBe("hidden");
    expect(document.body.style.position).toBe("fixed");

    fireEvent.keyDown(screen.getByRole("dialog", { name: "가는 날 선택" }), { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "가는 날 선택" })).toBeNull();
    expect(document.documentElement.style.overflow).toBe("");
    expect(document.body.style.position).toBe("");
    expect(screen.getByRole("button", { name: "실시간 알림 접기" })).toBeTruthy();
  });
});
