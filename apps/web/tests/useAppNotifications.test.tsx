import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import type { AppNotificationInput } from "../src/features/app/notificationCenter";
import {
  loadNotificationDismissalLedger,
  NOTIFICATION_DISMISSAL_STORAGE_KEY,
} from "../src/features/app/notificationDismissalStorage";
import { useAppNotifications } from "../src/features/app/useAppNotifications";

function stickyNotice(overrides: Partial<AppNotificationInput> = {}): AppNotificationInput {
  return {
    title: "예매 결과를 확인해야 합니다",
    subjectKey: "watch:one",
    revisionKey: "watch:one:manual:terminal",
    revisionAt: "2026-08-03T12:10:00Z",
    kind: "manual_check",
    ...overrides,
  };
}

describe("useAppNotifications dismissal persistence", () => {
  beforeEach(() => window.localStorage.clear());

  it("keeps explicit sticky dismissals across clear and remount while allowing a newer revision", () => {
    const first = renderHook(() => useAppNotifications());
    act(() => first.result.current.push(stickyNotice()));
    const dismissedId = first.result.current.state.notices[0]?.id;
    expect(dismissedId).toBeDefined();
    if (dismissedId === undefined) throw new Error("sticky notice was not created");

    act(() => first.result.current.dismiss(dismissedId));
    expect(loadNotificationDismissalLedger()).toEqual([{
      subjectKey: "watch:one",
      revisionKey: "watch:one:manual:terminal",
      revisionAt: "2026-08-03T12:10:00Z",
      lifecyclePhase: 2,
    }]);

    act(() => first.result.current.clear());
    act(() => first.result.current.pushMany([
      stickyNotice(),
      stickyNotice({
        title: "늦게 도착한 예매 진행",
        revisionKey: "watch:one:progress:older",
        revisionAt: "2026-08-03T12:09:00Z",
        kind: "reserving",
      }),
    ]));
    expect(first.result.current.state.notices).toEqual([]);

    act(() => first.result.current.push(stickyNotice({
      title: "새 공식 결과",
      revisionKey: "watch:one:manual:newer",
      revisionAt: "2026-08-03T12:11:00Z",
    })));
    expect(first.result.current.state.notices.map((notice) => notice.title))
      .toEqual(["새 공식 결과"]);

    act(() => first.result.current.clear());
    expect(first.result.current.state.notices).toEqual([]);
    expect(loadNotificationDismissalLedger()).toHaveLength(1);
    first.unmount();

    const remounted = renderHook(() => useAppNotifications());
    act(() => remounted.result.current.pushMany([
      stickyNotice(),
      stickyNotice({
        title: "늦게 도착한 예매 진행",
        revisionKey: "watch:one:progress:older-after-remount",
        revisionAt: "2026-08-03T12:09:30Z",
        kind: "reserving",
      }),
    ]));
    expect(remounted.result.current.state.notices).toEqual([]);

    act(() => remounted.result.current.push(stickyNotice({
      title: "재접속 뒤 새 공식 결과",
      revisionKey: "watch:one:manual:newer-after-remount",
      revisionAt: "2026-08-03T12:12:00Z",
    })));
    expect(remounted.result.current.state.notices.map((notice) => notice.title))
      .toEqual(["재접속 뒤 새 공식 결과"]);
  });

  it("does not persist notices that were only shown or timed notices that close", () => {
    const shown = renderHook(() => useAppNotifications());
    act(() => shown.result.current.push(stickyNotice()));
    expect(loadNotificationDismissalLedger()).toEqual([]);
    shown.unmount();

    const shownAgain = renderHook(() => useAppNotifications());
    act(() => shownAgain.result.current.push(stickyNotice()));
    expect(shownAgain.result.current.state.notices).toHaveLength(1);
    shownAgain.unmount();

    window.localStorage.removeItem(NOTIFICATION_DISMISSAL_STORAGE_KEY);
    const timed = renderHook(() => useAppNotifications());
    act(() => timed.result.current.push({
      title: "일반 안내",
      subjectKey: "generic:one",
      revisionKey: "generic:one:1",
      revisionAt: "2026-08-03T12:10:00Z",
      kind: "generic",
    }));
    const timedId = timed.result.current.state.notices[0]?.id;
    expect(timedId).toBeDefined();
    if (timedId === undefined) throw new Error("timed notice was not created");
    act(() => timed.result.current.dismiss(timedId));
    expect(loadNotificationDismissalLedger()).toEqual([]);
    timed.unmount();

    const timedAgain = renderHook(() => useAppNotifications());
    act(() => timedAgain.result.current.push({
      title: "일반 안내",
      subjectKey: "generic:one",
      revisionKey: "generic:one:1",
      revisionAt: "2026-08-03T12:10:00Z",
      kind: "generic",
    }));
    expect(timedAgain.result.current.state.notices).toHaveLength(1);
  });

  it("exposes a stable automatic prune that does not persist a user dismissal", () => {
    const rendered = renderHook(() => useAppNotifications());
    const initialPrune = rendered.result.current.pruneStaleSubjects;
    rendered.rerender();
    expect(rendered.result.current.pruneStaleSubjects).toBe(initialPrune);

    act(() => rendered.result.current.pushMany([
      stickyNotice(),
      {
        title: "감시 상태가 변경되었습니다",
        subjectKey: "watch:timed",
        revisionKey: "watch:timed:recovery",
        revisionAt: "2026-08-03T12:10:00Z",
        kind: "recovery",
      },
    ]));
    act(() => rendered.result.current.pruneStaleSubjects(["watch:one", "watch:timed"]));

    expect(rendered.result.current.state.notices.map((notice) => notice.subjectKey))
      .toEqual(["watch:timed"]);
    expect(loadNotificationDismissalLedger()).toEqual([]);
    rendered.unmount();

    const remounted = renderHook(() => useAppNotifications());
    act(() => remounted.result.current.push(stickyNotice()));
    expect(remounted.result.current.state.notices).toContainEqual(expect.objectContaining({
      subjectKey: "watch:one",
    }));
  });
});
