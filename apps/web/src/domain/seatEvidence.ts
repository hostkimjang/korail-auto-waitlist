interface SeatProvenance {
  kind?: string;
  source?: string;
  observed_at?: string;
  fresh_until?: string;
  client_freshness?: {
    ttl_ms?: number;
    received_monotonic_ms?: number;
  };
}

interface SeatWithProvenance {
  provenance?: SeatProvenance;
}

export function hasObservedSeatEvidence(seat: SeatWithProvenance | null | undefined): boolean {
  const provenance = seat?.provenance;
  if (!provenance || ![
    "official_provider",
    "official_page_browser_companion",
    "user_confirmed_official_page",
    "mock",
  ].includes(provenance.kind ?? "")) return false;
  if (typeof provenance.source !== "string" || !provenance.source.trim()) return false;
  if (
    typeof provenance.observed_at !== "string"
    || !/(?:Z|[+-]\d{2}:\d{2})$/i.test(provenance.observed_at)
    || !Number.isFinite(Date.parse(provenance.observed_at))
  ) return false;
  if (![
    "official_page_browser_companion",
    "user_confirmed_official_page",
  ].includes(provenance.kind ?? "")) return true;

  const expectedSource = provenance.kind === "official_page_browser_companion"
    ? "korail-official-browser-companion"
    : "official-page-user-confirmation";
  if (provenance.source !== expectedSource) return false;
  if (
    typeof provenance.fresh_until !== "string"
    || !/(?:Z|[+-]\d{2}:\d{2})$/i.test(provenance.fresh_until)
    || !Number.isFinite(Date.parse(provenance.fresh_until))
  ) return false;
  const serverTtl = Date.parse(provenance.fresh_until) - Date.parse(provenance.observed_at);
  if (serverTtl <= 0 || serverTtl > 5 * 60 * 1000) return false;
  const freshness = provenance.client_freshness;
  if (!freshness || freshness.ttl_ms !== serverTtl) return false;
  if (
    !Number.isFinite(freshness.received_monotonic_ms)
    || (freshness.received_monotonic_ms ?? -1) < 0
  ) return false;
  if (typeof performance === "undefined" || typeof performance.now !== "function") return false;
  const elapsed = performance.now() - (freshness.received_monotonic_ms ?? 0);
  return Number.isFinite(elapsed) && elapsed >= 0 && elapsed < serverTtl;
}
