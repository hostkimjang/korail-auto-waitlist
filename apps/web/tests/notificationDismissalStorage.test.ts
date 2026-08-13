import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MAX_NOTIFICATION_REVISION_HISTORY } from "../src/features/app/notificationCenter";
import {
  loadNotificationDismissalLedger,
  NOTIFICATION_DISMISSAL_STORAGE_KEY,
  saveNotificationDismissalLedger,
} from "../src/features/app/notificationDismissalStorage";

describe("notification dismissal storage", () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => vi.useRealTimers());

  it("validates the versioned unknown boundary and keeps only valid entries", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-12T12:00:00Z"));
    window.localStorage.setItem(NOTIFICATION_DISMISSAL_STORAGE_KEY, JSON.stringify({
      version: 1,
      entries: [
        {
          subjectKey: "watch:one",
          revisionKey: "watch:one:manual:1",
          revisionAt: "2026-08-03T12:10:00Z",
          lifecyclePhase: 2,
        },
        {
          subjectKey: "watch:invalid-time",
          revisionKey: "watch:invalid-time:1",
          revisionAt: "not-a-time",
          lifecyclePhase: 2,
        },
        {
          subjectKey: "watch:timezone-less",
          revisionKey: "watch:timezone-less:1",
          revisionAt: "2026-08-12T12:00:00",
          lifecyclePhase: 2,
        },
        {
          subjectKey: "watch:far-future",
          revisionKey: "watch:far-future:1",
          revisionAt: "9999-08-12T12:00:00Z",
          lifecyclePhase: 2,
        },
        {
          subjectKey: "",
          revisionKey: "watch:empty-subject:1",
          revisionAt: null,
          lifecyclePhase: 1,
        },
        null,
      ],
    }));

    expect(loadNotificationDismissalLedger()).toEqual([{
      subjectKey: "watch:one",
      revisionKey: "watch:one:manual:1",
      revisionAt: "2026-08-03T12:10:00Z",
      lifecyclePhase: 2,
    }]);

    window.localStorage.setItem(NOTIFICATION_DISMISSAL_STORAGE_KEY, "{broken");
    expect(loadNotificationDismissalLedger()).toEqual([]);
    window.localStorage.setItem(NOTIFICATION_DISMISSAL_STORAGE_KEY, JSON.stringify({
      version: 2,
      entries: [],
    }));
    expect(loadNotificationDismissalLedger()).toEqual([]);
  });

  it("deduplicates revisions and bounds the persisted ledger to the revision history limit", () => {
    const entries = Array.from(
      { length: MAX_NOTIFICATION_REVISION_HISTORY + 1 },
      (_, index) => ({
        subjectKey: `watch:${index}`,
        revisionKey: `watch:${index}:revision`,
        revisionAt: `2026-08-03T12:${String(index % 60).padStart(2, "0")}:00Z`,
        lifecyclePhase: 2 as const,
      }),
    );
    entries.push({
      subjectKey: `watch:${MAX_NOTIFICATION_REVISION_HISTORY}`,
      revisionKey: `watch:${MAX_NOTIFICATION_REVISION_HISTORY}:revision`,
      revisionAt: "2026-08-03T12:00:00Z",
      lifecyclePhase: 2,
    });

    saveNotificationDismissalLedger(entries);

    const restored = loadNotificationDismissalLedger();
    expect(restored).toHaveLength(MAX_NOTIFICATION_REVISION_HISTORY);
    expect(restored[0]?.revisionKey).toBe("watch:1:revision");
    expect(restored.at(-1)?.revisionKey)
      .toBe(`watch:${MAX_NOTIFICATION_REVISION_HISTORY}:revision`);
  });

  it("merges stale writers so two open tabs cannot overwrite each other's dismissals", () => {
    const dismissedInFirstTab = {
      subjectKey: "watch:first",
      revisionKey: "watch:first:payment:1",
      revisionAt: "2026-08-03T12:00:00Z",
      lifecyclePhase: 2 as const,
    };
    const dismissedInSecondTab = {
      subjectKey: "watch:second",
      revisionKey: "watch:second:payment:1",
      revisionAt: "2026-08-03T12:01:00Z",
      lifecyclePhase: 2 as const,
    };

    saveNotificationDismissalLedger([dismissedInFirstTab]);
    saveNotificationDismissalLedger([dismissedInSecondTab]);

    expect(loadNotificationDismissalLedger()).toEqual([
      dismissedInFirstTab,
      dismissedInSecondTab,
    ]);
  });
});
