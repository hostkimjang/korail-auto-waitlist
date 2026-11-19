import { CheckCircle, CreditCard, Info } from "@phosphor-icons/react";

import type { ProviderAccount, RailProvider } from "../../api/providerAccounts";
import type { AutomaticReservationAvailability } from "../../api/providerCapabilities";
import {
  canReserveAutomatically,
  providersNeedingAccountLogin,
  providersWithoutAutomaticReservation,
  type ReservationPolicy,
} from "./reservationPolicy";

export interface ReservationPolicyControlProps {
  value: ReservationPolicy;
  selectedProviders: ReadonlyArray<RailProvider>;
  accounts: ReadonlyArray<ProviderAccount>;
  automaticReservation: AutomaticReservationAvailability | null;
  onChange: (value: ReservationPolicy) => void;
}

// Signing in cannot help while the installation switch is off, so that reason comes first.
function unavailableReason(
  selectedProviders: ReadonlyArray<RailProvider>,
  accounts: ReadonlyArray<ProviderAccount>,
  automaticReservation: AutomaticReservationAvailability | null,
): string {
  if (automaticReservation === null) {
    return "이 설치의 자동 예매 설정을 아직 확인하지 못했습니다. 확인되면 이 옵션을 고를 수 있습니다.";
  }
  const switchedOff = providersWithoutAutomaticReservation(selectedProviders, automaticReservation);
  if (switchedOff.length > 0) {
    return `이 설치는 ${switchedOff.join(" · ")} 자동 예매가 꺼져 있어 알림만 받을 수 있습니다. 설치한 컴퓨터에서 railwait configure reservation on으로 켤 수 있습니다.`;
  }
  return `${providersNeedingAccountLogin(selectedProviders, accounts).join(" · ")} 계정 로그인을 확인해야 이 옵션을 사용할 수 있습니다.`;
}

export function ReservationPolicyControl({
  value,
  selectedProviders,
  accounts,
  automaticReservation,
  onChange,
}: ReservationPolicyControlProps) {
  const canReserve = canReserveAutomatically(selectedProviders, accounts, automaticReservation);
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
        <p id="reservation-policy-payment-help">결제 필요 알림을 받은 뒤 공식 플랫폼에서 직접 결제하세요. 결제 없이 임시 예약이 끝나면 좌석 감시를 다시 시작하며, 자동 예매 재시도에는 사용자 확인이 필요합니다.</p>
      ) : (
        <p id="reservation-policy-account-help" className="reservation-policy-warning">
          {unavailableReason(selectedProviders, accounts, automaticReservation)}
        </p>
      )}
    </fieldset>
  );
}
