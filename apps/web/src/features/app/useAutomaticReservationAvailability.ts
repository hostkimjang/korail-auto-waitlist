import { useEffect, useState } from "react";

import {
  fetchAutomaticReservationAvailability,
  type AutomaticReservationAvailability,
} from "../../api/providerCapabilities";

const demoAvailability: AutomaticReservationAvailability = { KORAIL: true, SRT: true };

export interface UseAutomaticReservationAvailabilityOptions {
  authenticated: boolean;
  demo: boolean;
  /**
   * Reloads when it changes, such as on navigation, so a switch changed on the
   * host appears without reloading the page.
   */
  revalidateKey?: string;
  load?: () => Promise<AutomaticReservationAvailability>;
}

/**
 * Returns `null` until the server answers. Callers treat that as unavailable, so a
 * new wait never defaults to automatic reservation the server would not run.
 */
export function useAutomaticReservationAvailability({
  authenticated,
  demo,
  revalidateKey,
  load = fetchAutomaticReservationAvailability,
}: UseAutomaticReservationAvailabilityOptions): AutomaticReservationAvailability | null {
  const [loaded, setLoaded] = useState<AutomaticReservationAvailability | null>(null);

  useEffect(() => {
    if (demo || !authenticated) return undefined;
    let active = true;
    // A failed reload keeps the last answer instead of flipping every control.
    load().then(
      (value) => { if (active) setLoaded(value); },
      () => undefined,
    );
    return () => {
      active = false;
    };
  }, [authenticated, demo, load, revalidateKey]);

  if (demo) return demoAvailability;
  return authenticated ? loaded : null;
}
