import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { pushNotifications, initialNotificationCenterState } from "../src/features/app/notificationCenter";
import { AppNotificationCenter } from "../src/features/app/AppNotificationCenter";

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
      autoCloseMs: 30_000,
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
  it("renders one region, groups simultaneous events, and does not steal focus", () => {
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

  it("collapses without pausing timed dismissal and exposes one batch live announcement", () => {
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
    expect(announcement.textContent).toContain("좌석 발견 2건");
    expect(announcement.textContent).toContain("예매 진행 1건");
    fireEvent.click(screen.getByRole("button", { name: "실시간 알림 접기" }));
    expect(screen.getByRole("button", { name: "실시간 알림 펼치기" })).toBeTruthy();
    expect(
      screen.getByText("첫 번째 좌석").closest(".notification-center-body")?.hasAttribute("hidden"),
    ).toBe(true);
    vi.advanceTimersByTime(30_000);
    expect(onDismiss).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });
});
