import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  fetchUiPreferences,
  updateUiPreferences,
} from "../src/api/uiPreferences";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("UI preferences API boundary", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    Object.defineProperty(document, "cookie", {
      configurable: true,
      writable: true,
      value: "rail_csrf=csrf%20token",
    });
  });

  it("maps a bounded unified observation interval and rejects malformed values", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({
        timetable_refresh_interval_seconds: 1,
        observation_interval_seconds: 1,
        preferences_updated_at: "2026-07-31T06:00:00Z",
      }))
      .mockResolvedValueOnce(response({
        timetable_refresh_interval_seconds: 0,
        observation_interval_seconds: 0,
        preferences_updated_at: "not-a-timestamp",
      }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchUiPreferences()).resolves.toEqual({
      timetableRefreshIntervalSeconds: 1,
      seatObservationIntervalSeconds: 1,
      updatedAt: "2026-07-31T06:00:00Z",
    });
    await expect(fetchUiPreferences()).rejects.toThrow("응답이 올바르지 않습니다");
  });

  it("PATCHes the unified observation interval with CSRF protection", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({
      timetable_refresh_interval_seconds: 45,
      observation_interval_seconds: 5,
      preferences_updated_at: "2026-07-31T06:00:00Z",
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(updateUiPreferences({
      timetableRefreshIntervalSeconds: 45,
      seatObservationIntervalSeconds: 5,
    })).resolves.toMatchObject({
      timetableRefreshIntervalSeconds: 45,
      seatObservationIntervalSeconds: 5,
    });

    expect(fetchMock).toHaveBeenCalledWith("/api/v1/preferences/ui", expect.objectContaining({
      method: "PATCH",
      credentials: "include",
      cache: "no-store",
      body: JSON.stringify({
        timetable_refresh_interval_seconds: 45,
        observation_interval_seconds: 5,
      }),
    }));
    const [, request] = fetchMock.mock.calls[0] ?? [];
    expect(request.headers.get("Content-Type")).toBe("application/json");
    expect(request.headers.get("X-CSRF-Token")).toBe("csrf token");
  });
});
