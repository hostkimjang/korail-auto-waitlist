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
      steps: [{
        label: "예매 요청 완료",
        state: "completed",
        occurredAt: "2026-08-03T12:09:48Z",
        durationMs: 3_000,
        durationPrefix: "이전 단계 후",
      }],
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
  it("mounts detail cards only while expanded and keeps the controlled body target", async () => {
    const state = pushNotifications(centerState(), [{
      title: "감시 상태 변경",
      subjectKey: "watch:four",
      revisionKey: "watch:four:1",
      kind: "recovery",
    }]);
    const onExpandedChange = vi.fn();
    const { container, rerender } = render(
      <AppNotificationCenter
        state={state}
        expanded={false}
        onExpandedChange={onExpandedChange}
        onDismiss={vi.fn()}
        onDismissGroup={vi.fn()}
        onDismissTimed={vi.fn()}
      />,
    );

    const toggle = screen.getByRole("button", { name: "실시간 알림 펼치기" });
    const body = container.querySelector<HTMLDivElement>("#notification-center-body");
    expect(toggle.getAttribute("aria-controls")).toBe("notification-center-body");
    expect(body).not.toBeNull();
    expect(body?.hidden).toBe(true);
    expect(container.querySelectorAll(".notification-group")).toHaveLength(0);
    expect(container.querySelectorAll(".notification-card")).toHaveLength(0);
    expect(container.querySelector(".notification-center-footer")).toBeNull();
    expect(screen.getByText("실시간 알림").nextElementSibling?.textContent).toBe("5건");
    expect(await screen.findByText("첫 번째 좌석", {
      selector: ".notification-center-peek strong",
    })).toBeTruthy();

    rerender(
      <AppNotificationCenter
        state={state}
        expanded
        onExpandedChange={onExpandedChange}
        onDismiss={vi.fn()}
        onDismissGroup={vi.fn()}
        onDismissTimed={vi.fn()}
      />,
    );
    expect(body?.hidden).toBe(false);
    expect(container.querySelectorAll(".notification-group")).toHaveLength(4);
    expect(container.querySelectorAll(".notification-card")).toHaveLength(5);
    expect(container.querySelector(".notification-center-footer")).not.toBeNull();

    rerender(
      <AppNotificationCenter
        state={state}
        expanded={false}
        onExpandedChange={onExpandedChange}
        onDismiss={vi.fn()}
        onDismissGroup={vi.fn()}
        onDismissTimed={vi.fn()}
      />,
    );
    expect(body?.hidden).toBe(true);
    expect(container.querySelectorAll(".notification-group")).toHaveLength(0);
    expect(container.querySelectorAll(".notification-card")).toHaveLength(0);
    expect(container.querySelector(".notification-center-footer")).toBeNull();
  });

  it("renders one region, groups simultaneous events, and does not steal focus", async () => {
    const onDismissGroup = vi.fn();
    const anchor = document.createElement("button");
    document.body.append(anchor);
    anchor.focus();
    const state = centerState();
    const onExpandedChange = vi.fn();

    const { rerender } = render(
      <AppNotificationCenter
        state={state}
        expanded={false}
        onExpandedChange={onExpandedChange}
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
    expect(onExpandedChange).toHaveBeenCalledWith(true);
    rerender(
      <AppNotificationCenter
        state={state}
        expanded
        onExpandedChange={onExpandedChange}
        onDismiss={vi.fn()}
        onDismissGroup={onDismissGroup}
        onDismissTimed={vi.fn()}
      />,
    );
    const timestamp = within(center).getByText("21:16:34");
    expect(timestamp.tagName).toBe("TIME");
    expect(timestamp.getAttribute("datetime")).toBe("2026-08-03T12:16:34Z");
    expect(timestamp.getAttribute("aria-label")).toBe("알림 발생 시각 21:16:34");
    expect(within(center).getByLabelText("예매 작업 시간").textContent)
      .toContain("시작 21:09:45전체 3.3초");
    expect(within(center).getByText("예매 요청 완료").closest("li")?.textContent)
      .toContain("21:09:48이전 단계 후 3.0초");
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
    const onExpandedChange = vi.fn();
    render(
      <AppNotificationCenter
        state={centerState()}
        expanded={false}
        onExpandedChange={onExpandedChange}
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
    expect(onExpandedChange).toHaveBeenCalledWith(true);
    expect(document.documentElement.style.overflow).toBe("");
    expect(document.body.style.position).toBe("");
    act(() => vi.advanceTimersByTime(22_000));
    expect(onDismiss).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });

  it("dismisses the persistent notice from the foreground preview close button", async () => {
    const onDismiss = vi.fn();
    const state = centerState();
    render(
      <AppNotificationCenter
        state={state}
        expanded={false}
        onExpandedChange={vi.fn()}
        onDismiss={onDismiss}
        onDismissGroup={vi.fn()}
        onDismissTimed={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole("button", {
      name: "첫 번째 좌석 알림 닫기",
    }));

    expect(document.querySelector(".notification-center-peek")).toBeNull();
    expect(onDismiss).toHaveBeenCalledOnce();
    expect(onDismiss).toHaveBeenCalledWith(state.notices[0]?.id);
  });

  it("keeps expanded notifications non-blocking while a real modal owns the scroll lock", () => {
    render(
      <>
        <AppNotificationCenter
          state={centerState()}
          expanded
          onExpandedChange={vi.fn()}
          onDismiss={vi.fn()}
          onDismissGroup={vi.fn()}
          onDismissTimed={vi.fn()}
        />
        <CalendarPicker value="2026-08-08" onChange={vi.fn()} />
      </>,
    );

    expect(screen.getByRole("button", { name: "실시간 알림 접기" })).toBeTruthy();
    expect(document.documentElement.style.overflow).toBe("");
    expect(document.body.style.position).toBe("");

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

  it("opens as a non-blocking empty center when controlled by the mobile bell", () => {
    render(
      <AppNotificationCenter
        state={initialNotificationCenterState}
        expanded
        onExpandedChange={vi.fn()}
        onDismiss={vi.fn()}
        onDismissGroup={vi.fn()}
        onDismissTimed={vi.fn()}
      />,
    );

    expect(screen.getByRole("region", { name: "실시간 알림" })).toBeTruthy();
    expect(screen.getByText("새 실시간 알림이 없습니다.")).toBeTruthy();
    expect(document.documentElement.style.overflow).toBe("");
    expect(document.body.style.position).toBe("");
  });
});
