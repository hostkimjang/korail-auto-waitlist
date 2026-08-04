import { CheckCircle, CreditCard, Info } from "@phosphor-icons/react";

import type { ProviderAccount, RailProvider } from "../../api/providerAccounts";
import {
  canReserveWithAuthenticatedAccounts,
  type ReservationPolicy,
} from "./reservationPolicy";

export interface ReservationPolicyControlProps {
  value: ReservationPolicy;
  selectedProviders: ReadonlyArray<RailProvider>;
  accounts: ReadonlyArray<ProviderAccount>;
  onChange: (value: ReservationPolicy) => void;
}

export function ReservationPolicyControl({ value, selectedProviders, accounts, onChange }: ReservationPolicyControlProps) {
  const missingProviders = selectedProviders.filter((provider) => {
    const account = accounts.find((item) => item.provider === provider);
    return !account?.configured || !account.enabled || account.lastAuthStatus !== "authenticated";
  });
  const canReserve = canReserveWithAuthenticatedAccounts(selectedProviders, accounts);
  const automaticSelected = value === "reserve_once_before_payment";

  return (
    <fieldset className="reservation-policy-control">
      <legend>좌석 발견 후 행동</legend>
      <button
        type="button"
        className={!automaticSelected ? "reservation-policy-option is-selected" : "reservation-policy-option"}
        aria-pressed={!automaticSelected}
        onClick={() => onChange("notify_only")}
      >
        <Info aria-hidden="true" />
        <span><strong>알림만 받기</strong><small>좌석을 찾으면 알리고 계속 감시합니다.</small></span>
        {!automaticSelected ? <CheckCircle weight="fill" aria-hidden="true" /> : null}
      </button>
      <button
        type="button"
        className={automaticSelected ? "reservation-policy-option is-selected" : "reservation-policy-option"}
        aria-pressed={automaticSelected}
        aria-describedby={!canReserve ? "reservation-policy-account-help" : "reservation-policy-payment-help"}
        disabled={!canReserve}
        onClick={() => onChange("reserve_once_before_payment")}
      >
        <CreditCard aria-hidden="true" />
        <span><strong>좌석 재발견마다 자동 예매</strong><small>새 좌석 가용성 에피소드마다 예매하고 결제 전에 멈춥니다. 같은 에피소드에서는 중복 요청하지 않습니다.</small></span>
        {automaticSelected ? <CheckCircle weight="fill" aria-hidden="true" /> : null}
      </button>
      {canReserve ? (
        <p id="reservation-policy-payment-help">자동 결제는 하지 않습니다. 결제 필요 알림을 받은 뒤 공식 플랫폼에서 직접 결제하세요.</p>
      ) : (
        <p id="reservation-policy-account-help" className="reservation-policy-warning">
          {missingProviders.join(" · ")} 계정 로그인을 확인해야 이 옵션을 사용할 수 있습니다.
        </p>
      )}
    </fieldset>
  );
}
