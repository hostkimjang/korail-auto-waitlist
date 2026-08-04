import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const authApi = vi.hoisted(() => ({
  DEMO_MODE: false,
  getAuthStatus: vi.fn(),
}));

vi.mock("../src/api.js", () => authApi);

import { useAuthState } from "../src/features/auth/useAuthState";

afterEach(() => {
  vi.clearAllMocks();
});

describe("useAuthState", () => {
  it("does not disguise a status failure as configured and recovers on retry", async () => {
    authApi.getAuthStatus
      .mockRejectedValueOnce(new Error("상태 조회 실패"))
      .mockResolvedValueOnce({ configured: false, authenticated: false, registration_allowed: true });

    const { result } = renderHook(() => useAuthState());

    await waitFor(() => expect(result.current.auth.error).toBe("상태 조회 실패"));
    expect(result.current.auth.configured).toBe(false);
    expect(result.current.auth.registrationAllowed).toBe(false);

    await act(async () => {
      await result.current.retryAuthStatus();
    });

    expect(result.current.auth).toMatchObject({
      loading: false,
      configured: false,
      authenticated: false,
      registrationAllowed: true,
      error: null,
    });
  });
});
