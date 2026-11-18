import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ReservationPolicyControl } from "../src/features/new-wait/ReservationPolicyControl";
import {
  canReserveAutomatically,
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

const reservationOn = { KORAIL: true, SRT: true } as const;

describe("reservation policy control", () => {
  it("defaults an authenticated selected provider to per-episode automatic reservation", () => {
    expect(canReserveAutomatically(["KORAIL"], [configuredKorail], reservationOn)).toBe(true);
    expect(defaultReservationPolicy(["KORAIL"], [configuredKorail], reservationOn)).toBe(
      "reserve_once_before_payment",
    );
    expect(defaultReservationPolicy(
      ["KORAIL"],
      [{ ...configuredKorail, lastAuthStatus: "auth_required" }],
      reservationOn,
    )).toBe("notify_only");
  });

  it("defaults to notifications while the installation switch is off or unknown", () => {
    for (const availability of [{ KORAIL: false, SRT: true }, null]) {
      expect(canReserveAutomatically(["KORAIL"], [configuredKorail], availability)).toBe(false);
      expect(defaultReservationPolicy(["KORAIL"], [configuredKorail], availability)).toBe("notify_only");
    }
  });

  it("keeps automatic reservation unavailable until every selected provider is configured", () => {
    render(<ReservationPolicyControl
      value="notify_only"
      selectedProviders={["KORAIL", "SRT"]}
      accounts={[configuredKorail]}
      automaticReservation={reservationOn}
      onChange={vi.fn()}
    />);

    const automaticButton = screen.getByRole("button", { name: /자동 예매/ });
    if (!(automaticButton instanceof HTMLButtonElement)) throw new Error("예매 정책 버튼을 찾을 수 없습니다.");
    expect(automaticButton.disabled).toBe(true);
    expect(screen.getByText(/SRT 계정 로그인을 확인/)).toBeTruthy();
  });

  it("names the installation switch instead of asking to sign in when it is off", () => {
    render(<ReservationPolicyControl
      value="notify_only"
      selectedProviders={["KORAIL"]}
      accounts={[configuredKorail]}
      automaticReservation={{ KORAIL: false, SRT: true }}
      onChange={vi.fn()}
    />);

    const automaticButton = screen.getByRole("button", { name: /자동 예매/ });
    if (!(automaticButton instanceof HTMLButtonElement)) throw new Error("예매 정책 버튼을 찾을 수 없습니다.");
    expect(automaticButton.disabled).toBe(true);
    expect(screen.getByText(/이 설치는 KORAIL 자동 예매가 꺼져 있어 알림만 받을 수 있습니다/)).toBeTruthy();
    expect(screen.getByText(/railwait configure reservation on/)).toBeTruthy();
    expect(screen.queryByText(/계정 로그인을 확인/)).toBeNull();
  });

  it("does not offer automatic reservation before the installation switch is known", () => {
    render(<ReservationPolicyControl
      value="notify_only"
      selectedProviders={["KORAIL"]}
      accounts={[configuredKorail]}
      automaticReservation={null}
      onChange={vi.fn()}
    />);

    const automaticButton = screen.getByRole("button", { name: /자동 예매/ });
    if (!(automaticButton instanceof HTMLButtonElement)) throw new Error("예매 정책 버튼을 찾을 수 없습니다.");
    expect(automaticButton.disabled).toBe(true);
    expect(screen.getByText(/자동 예매 설정을 아직 확인하지 못했습니다/)).toBeTruthy();
  });

  it("selects per-episode automatic reservation while preserving the direct-payment boundary", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<ReservationPolicyControl
      value="notify_only"
      selectedProviders={["KORAIL"]}
      accounts={[configuredKorail]}
      automaticReservation={reservationOn}
      onChange={onChange}
    />);

    await user.click(screen.getByRole("button", { name: /자동 예매/ }));
    expect(onChange).toHaveBeenCalledWith("reserve_once_before_payment");
    expect(screen.getByText(/새 좌석 가용성 에피소드마다 예매/)).toBeTruthy();
    expect(screen.getByText(/같은 에피소드에서는 중복 요청하지 않습니다/)).toBeTruthy();
    expect(screen.getByText(/자동 결제는 하지 않습니다/)).toBeTruthy();
  });
});
