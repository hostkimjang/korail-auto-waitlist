import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ReservationPolicyControl } from "../src/features/new-wait/ReservationPolicyControl";
import {
  canReserveWithAuthenticatedAccounts,
  defaultReservationPolicy,
} from "../src/features/new-wait/reservationPolicy";

const configuredKorail = {
  provider: "KORAIL" as const,
  configured: true,
  enabled: true,
  loginMethod: "membership_number" as const,
  maskedLoginId: "ra***er",
  credentialVersion: 1,
  lastAuthStatus: "authenticated" as const,
  lastAuthenticatedAt: null,
  updatedAt: null,
};

describe("reservation policy control", () => {
  it("defaults an authenticated selected provider to per-episode automatic reservation", () => {
    expect(canReserveWithAuthenticatedAccounts(["KORAIL"], [configuredKorail])).toBe(true);
    expect(defaultReservationPolicy(["KORAIL"], [configuredKorail])).toBe(
      "reserve_once_before_payment",
    );
    expect(defaultReservationPolicy(
      ["KORAIL"],
      [{ ...configuredKorail, lastAuthStatus: "auth_required" }],
    )).toBe("notify_only");
  });

  it("keeps automatic reservation unavailable until every selected provider is configured", () => {
    render(<ReservationPolicyControl
      value="notify_only"
      selectedProviders={["KORAIL", "SRT"]}
      accounts={[configuredKorail]}
      onChange={vi.fn()}
    />);

    const automaticButton = screen.getByRole("button", { name: /자동 예매/ });
    if (!(automaticButton instanceof HTMLButtonElement)) throw new Error("예매 정책 버튼을 찾을 수 없습니다.");
    expect(automaticButton.disabled).toBe(true);
    expect(screen.getByText(/SRT 계정 로그인을 확인/)).toBeTruthy();
  });

  it("selects per-episode automatic reservation while preserving the direct-payment boundary", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<ReservationPolicyControl
      value="notify_only"
      selectedProviders={["KORAIL"]}
      accounts={[configuredKorail]}
      onChange={onChange}
    />);

    await user.click(screen.getByRole("button", { name: /자동 예매/ }));
    expect(onChange).toHaveBeenCalledWith("reserve_once_before_payment");
    expect(screen.getByText(/새 좌석 가용성 에피소드마다 예매/)).toBeTruthy();
    expect(screen.getByText(/같은 에피소드에서는 중복 요청하지 않습니다/)).toBeTruthy();
    expect(screen.getByText(/자동 결제는 하지 않습니다/)).toBeTruthy();
  });
});
