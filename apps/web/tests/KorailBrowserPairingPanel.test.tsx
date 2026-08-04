import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { KorailBrowserPairingPanel } from "../src/features/settings/KorailBrowserPairingPanel";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("KorailBrowserPairingPanel", () => {
  it("creates a five-minute one-time pairing code without exposing a shared env token", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "https://railwait.local");
      if (url.pathname.endsWith("/browser-companion/status")) {
        return response({ enabled: true, credentials: [] });
      }
      if (url.pathname.endsWith("/browser-companion/pairings") && init?.method === "POST") {
        return response({
          pairing_code: "pairing-code-only-visible-once-1234567890",
          expires_at: "2030-07-30T03:05:00Z",
        }, 201);
      }
      return response({ detail: "unexpected request" }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<KorailBrowserPairingPanel demo={false} onToast={vi.fn()} />);
    await screen.findByText("아직 연결된 브라우저가 없습니다.");
    await user.click(screen.getByRole("button", { name: /연결 코드 만들기/ }));

    expect(await screen.findByText("pairing-code-only-visible-once-1234567890")).toBeTruthy();
    expect(screen.queryByText(/KORAIL_BROWSER_BRIDGE_TOKEN/)).toBeNull();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });
});
