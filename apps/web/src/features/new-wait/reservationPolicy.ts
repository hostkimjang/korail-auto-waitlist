import type { ProviderAccount, RailProvider } from "../../api/providerAccounts";
import type { ReservationPolicy } from "../../domain/reservationPolicy";

export type { ReservationPolicy } from "../../domain/reservationPolicy";

export function canReserveWithAuthenticatedAccounts(
  selectedProviders: ReadonlyArray<RailProvider>,
  accounts: ReadonlyArray<ProviderAccount>,
): boolean {
  return selectedProviders.length > 0 && selectedProviders.every((provider) => {
    const account = accounts.find((item) => item.provider === provider);
    return account?.configured === true
      && account.enabled
      && account.lastAuthStatus === "authenticated";
  });
}

export function defaultReservationPolicy(
  selectedProviders: ReadonlyArray<RailProvider>,
  accounts: ReadonlyArray<ProviderAccount>,
): ReservationPolicy {
  return canReserveWithAuthenticatedAccounts(selectedProviders, accounts)
    ? "reserve_once_before_payment"
    : "notify_only";
}
