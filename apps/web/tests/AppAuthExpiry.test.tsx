import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const authApi = vi.hoisted(() => ({
  fetchWatches: vi.fn(),
  getAuthStatus: vi.fn(),
}));

vi.mock("../src/api.js", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/api.js")>();
  return {
    ...actual,
    DEMO_MODE: false,
    fetchWatches: authApi.fetchWatches,
    fetchNotificationChannels: vi.fn().mockResolvedValue([]),
    fetchProviders: vi.fn().mockResolvedValue([]),
    getAuthStatus: authApi.getAuthStatus,
    subscribeToEvents: vi.fn(() => () => undefined),
  };
});

vi.mock("../src/api/providerAccounts", () => ({
  fetchProviderAccounts: vi.fn().mockResolvedValue([]),
  saveProviderAccount: vi.fn(),
  deleteProviderAccount: vi.fn(),
}));

import { ApiError } from "../src/api.js";
import { App } from "../src/App.jsx";

describe("App authenticated data expiry", () => {
  it("returns to login when authenticated data loading receives 401", async () => {
    authApi.getAuthStatus.mockResolvedValue({
      configured: true,
      authenticated: true,
      registration_allowed: false,
    });
    authApi.fetchWatches.mockRejectedValue(new ApiError("session expired", 401));

    render(<App />);

    expect(await screen.findByRole("heading", { name: "관리자 로그인" })).toBeTruthy();
    expect(screen.getByLabelText("관리자 ID")).toBeTruthy();
  });
});
