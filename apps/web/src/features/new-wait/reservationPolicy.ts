import type { ProviderAccount, RailProvider } from "../../api/providerAccounts";
import type { AutomaticReservationAvailability } from "../../api/providerCapabilities";
import type { ReservationPolicy } from "../../domain/reservationPolicy";

export type { ReservationPolicy } from "../../domain/reservationPolicy";

/** Selected providers whose installation-wide reservation switch is off or not yet known. */
export function providersWithoutAutomaticReservation(
  selectedProviders: ReadonlyArray<RailProvider>,
  availability: AutomaticReservationAvailability | null,
): RailProvider[] {
  return selectedProviders.filter((provider) => availability?.[provider] !== true);
}

export function providersNeedingAccountLogin(
  selectedProviders: ReadonlyArray<RailProvider>,
  accounts: ReadonlyArray<ProviderAccount>,
): RailProvider[] {
  return selectedProviders.filter((provider) => {
    const account = accounts.find((item) => item.provider === provider);
    return !(account?.configured === true
      && account.enabled
      && account.lastAuthStatus === "authenticated");
  });
}

/**
 * Automatic reservation needs both the server switch and a signed-in account for
 * every selected provider. Either alone would register a watch that only notifies.
 */
export function canReserveAutomatically(
  selectedProviders: ReadonlyArray<RailProvider>,
  accounts: ReadonlyArray<ProviderAccount>,
  availability: AutomaticReservationAvailability | null,
): boolean {
  return selectedProviders.length > 0
    && providersWithoutAutomaticReservation(selectedProviders, availability).length === 0
    && providersNeedingAccountLogin(selectedProviders, accounts).length === 0;
}

export function defaultReservationPolicy(
  selectedProviders: ReadonlyArray<RailProvider>,
  accounts: ReadonlyArray<ProviderAccount>,
  availability: AutomaticReservationAvailability | null,
): ReservationPolicy {
  return canReserveAutomatically(selectedProviders, accounts, availability)
    ? "reserve_once_before_payment"
    : "notify_only";
}
