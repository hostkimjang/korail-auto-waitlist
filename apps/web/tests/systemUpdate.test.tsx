import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mapSystemUpdate, mapUpdateJob } from "../src/api/systemUpdate";
import { SystemUpdateSettings } from "../src/features/settings/SystemUpdateSettings";

const payload = { schema: 1, managed: true, manager_available: true, current_version: "1.3.3", latest_version: "1.4.0", plan_id: "a".repeat(64), available: true, reason: "update_available" };
function response(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
}
beforeEach(() => { sessionStorage.clear(); vi.restoreAllMocks(); });
describe("system update", () => {
  it("fails closed on unavailable managers and invalid plan identities", () => {
    expect(mapSystemUpdate(payload).available).toBe(true);
    expect(mapSystemUpdate({ ...payload, manager_available: false }).available).toBe(false);
    expect(mapSystemUpdate({ ...payload, plan_id: "../../evil" }).available).toBe(false);
    expect(() => mapUpdateJob({ schema: 1, id: "a".repeat(32), state: "secret-value" })).toThrow();
  });
  it("makes no requests in the demo", () => {
    const fetcher = vi.spyOn(globalThis, "fetch");
    render(<SystemUpdateSettings demo />);
    expect(screen.getByText(/체험 화면에서는/)).toBeTruthy();
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("requires the displayed plan confirmation and sends one idempotent request", async () => {
    const user = userEvent.setup();
    const id = "b".repeat(32);
    const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation(async (url, options) => {
      if (String(url).includes("/jobs/")) return response({ schema: 1, id, state: "succeeded", stage: "complete" });
      if (options?.method === "POST") return response({ schema: 1, id, state: "queued" }, 202);
      return response(payload);
    });
    render(<SystemUpdateSettings demo={false} />);
    const update = await screen.findByRole("button", { name: "v1.4.0 업데이트" });
    expect(update.hasAttribute("disabled")).toBe(true);
    await user.click(screen.getByRole("checkbox"));
    await user.click(update);
    await waitFor(() => expect(screen.getByText(/업데이트가 완료되었습니다/)).toBeTruthy());
    const posts = fetcher.mock.calls.filter((call) => call[1]?.method === "POST");
    expect(posts).toHaveLength(1);
    expect(JSON.parse(String(posts[0]?.[1]?.body))).toMatchObject({ target_release_id: "1.4.0", plan_id: payload.plan_id, idempotency_key: expect.stringMatching(/^[a-f0-9]{32}$/) });
  });
  it.each([
    ["untrusted or missing Origin", /지금 접속한 주소는 서버가 허용한 접속 주소가 아니어서/],
    ["CSRF validation failed", /보안 확인 값이 만료되었습니다/],
  ])("keeps a rejected update request visible after the follow-up refresh (%s)", async (detail, message) => {
    const user = userEvent.setup();
    const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation(async (_url, options) =>
      options?.method === "POST" ? response({ detail }, 403) : response(payload));
    render(<SystemUpdateSettings demo={false} />);
    const update = await screen.findByRole("button", { name: "v1.4.0 업데이트" });
    await user.click(screen.getByRole("checkbox"));
    await user.click(update);
    // The failed submission refreshes the update information. That successful refresh used to erase
    // the rejection, so the button looked unresponsive.
    await waitFor(() => expect(fetcher.mock.calls.filter((call) => call[1]?.method !== "POST")).toHaveLength(2));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.getByText(message)).toBeTruthy();
    expect(fetcher.mock.calls.filter((call) => call[1]?.method === "POST")).toHaveLength(1);
  });
  it("shows actionable legacy installation state without a failing button", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(response({ detail: "Not Found" }, 404));
    render(<SystemUpdateSettings demo={false} />);
    expect(await screen.findByText(/간편 업데이트가 아직 설치되지 않았습니다/)).toBeTruthy();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });
  it("resumes a submitted job after the view is reopened", async () => {
    const id = "d".repeat(32);
    sessionStorage.setItem("railwait-update-job", id);
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => response(String(url).includes("/jobs/")
      ? { schema: 1, id, state: "recovery_required", stage: "applying" } : payload));
    render(<SystemUpdateSettings demo={false} />);
    expect(await screen.findByText("복구 확인 필요")).toBeTruthy();
    expect(sessionStorage.getItem("railwait-update-job")).toBeNull();
  });
});
