import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  SETTINGS_JOB_STORAGE_KEY,
  useServerSettings,
  type ServerSettingsRequest,
} from "../src/features/settings/useServerSettings";
import {
  NEXT_SETTINGS_REVISION,
  SETTINGS_JOB_ID,
  jsonResponse,
  routeFetch,
  settingsJobPayload,
  settingsPayload,
} from "./serverSettingsFixtures";

const versionRequest: ServerSettingsRequest = {
  mode: "fields",
  fields: { VITE_SRT_MAIN_VALIDATED_VERSION: "2.0.41" },
  scope: "web",
  reloadRequired: true,
};
const switchRequest: ServerSettingsRequest = {
  mode: "fields",
  fields: { VITE_SRT_MAIN_DEEPLINK_ENABLED: "true", VITE_SRT_MAIN_VALIDATED_VERSION: "2.0.41" },
  scope: "web",
  reloadRequired: true,
};

function idempotencyKey(body: unknown): unknown {
  return typeof body === "object" && body !== null && "idempotency_key" in body ? body.idempotency_key : undefined;
}

beforeEach(() => sessionStorage.clear());
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("useServerSettings", () => {
  it("makes no requests in the demo", () => {
    const { spy } = routeFetch(() => jsonResponse(settingsPayload()));
    const { result } = renderHook(() => useServerSettings(true));
    expect(result.current.loading).toBe(false);
    expect(spy).not.toHaveBeenCalled();
  });

  it("reuses the idempotency key only when retrying the same unchanged request", async () => {
    let posts = 0;
    const { requests } = routeFetch(({ method, path }) => {
      if (method === "POST") {
        posts += 1;
        if (posts <= 2) throw new TypeError("network unavailable");
        return jsonResponse({ schema: 1, id: SETTINGS_JOB_ID, state: "queued" }, 202);
      }
      if (path.includes("/jobs/")) return jsonResponse(settingsJobPayload("rejected", "rejected", "E_SETTINGS_UNCHANGED"));
      return jsonResponse(settingsPayload());
    });
    const { result } = renderHook(() => useServerSettings(false));
    await waitFor(() => expect(result.current.snapshot).not.toBeNull());

    await act(async () => result.current.submit(versionRequest));
    expect(result.current.submitError?.message).toContain("같은 요청은 중복 실행하지 않습니다");
    await act(async () => result.current.submit(switchRequest));
    await act(async () => result.current.submit(switchRequest));
    await waitFor(() => expect(result.current.tracked?.job.state).toBe("rejected"));
    await act(async () => result.current.submit(switchRequest));

    const keys = requests.filter(({ method }) => method === "POST").map(({ body }) => idempotencyKey(body));
    expect(keys).toHaveLength(4);
    expect(keys[0]).toMatch(/^[a-f0-9]{32}$/);
    // An edited request is a new attempt; a network retry of it reuses the key; an accepted
    // request is finished, so the next submission starts another attempt.
    expect(keys[1]).not.toBe(keys[0]);
    expect(keys[2]).toBe(keys[1]);
    expect(keys[3]).not.toBe(keys[1]);
  });

  // Real time on purpose: the second poll is the 3-second production interval, and Testing
  // Library's async helpers wait on setTimeout, so faking it would stall them.
  it("keeps following a remembered job through a restart and reloads the snapshot when it ends", async () => {
    sessionStorage.setItem(SETTINGS_JOB_STORAGE_KEY, JSON.stringify({
      id: SETTINGS_JOB_ID,
      mode: "fields",
      scope: "app",
      reloadRequired: false,
    }));
    let jobPolls = 0;
    let settingsReads = 0;
    routeFetch(({ path }) => {
      if (path.includes("/jobs/")) {
        jobPolls += 1;
        return jsonResponse(jobPolls === 1 ? settingsJobPayload("running", "applying") : settingsJobPayload("succeeded", "succeeded"));
      }
      settingsReads += 1;
      return settingsReads === 1
        ? jsonResponse({ detail: "maintenance_in_progress" }, 503)
        : jsonResponse(settingsPayload({ revision: NEXT_SETTINGS_REVISION }));
    });
    const { result } = renderHook(() => useServerSettings(false));
    await waitFor(() => expect(result.current.tracked?.job.stage).toBe("applying"));
    await waitFor(() => expect(result.current.loadError).toContain("다시 시작되는 중"));
    expect(result.current.busy).toBe(true);

    await waitFor(
      () => expect(result.current.snapshot).toMatchObject({ available: true, revision: NEXT_SETTINGS_REVISION }),
      { timeout: 6000 },
    );
    expect(jobPolls).toBe(2);
    expect(result.current.tracked?.job.state).toBe("succeeded");
    expect(result.current.busy).toBe(false);
    expect(result.current.loadError).toBeNull();
    expect(sessionStorage.getItem(SETTINGS_JOB_STORAGE_KEY)).toBeNull();
  }, 10_000);

  it.each([
    [409, "settings_changed", "다른 곳에서 설정이 바뀌었습니다", null, 2],
    [409, "settings_manager_upgrade_required", "관리 서비스를 한 번 교체해야", "manager_upgrade", 1],
    [503, "settings_manager_unavailable", "관리 서비스와 Docker 실행 상태", null, 1],
    [422, "request_validation_failed", "입력값을 서버가 받아들이지 않았습니다", null, 1],
    [401, "authentication required", "다시 로그인한 뒤 다시 적용해 주세요", null, 1],
    [403, "untrusted or missing Origin", "설정 변경 요청이 거부되었습니다", null, 1],
    [403, "CSRF validation failed", "보안 확인 값이 만료되었습니다", null, 1],
  ])("maps a %s %s rejection", async (status, detail, message, guidance, settingsReads) => {
    const { requests } = routeFetch(({ method }) => (method === "POST"
      ? jsonResponse({ detail }, status)
      : jsonResponse(settingsPayload())));
    const { result } = renderHook(() => useServerSettings(false));
    await waitFor(() => expect(result.current.snapshot).not.toBeNull());
    await act(async () => result.current.submit(versionRequest));
    expect(result.current.submitError?.message).toContain(message);
    expect(result.current.submitError?.guidance).toBe(guidance);
    await waitFor(() => expect(requests.filter(({ method }) => method === "GET")).toHaveLength(settingsReads));
    expect(result.current.tracked).toBeNull();
  });
});
