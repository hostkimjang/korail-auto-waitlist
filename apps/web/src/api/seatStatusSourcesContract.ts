export type SeatStatusProvider = "korail" | "srt";
export type SeatStatusSourceName = "korail_browser" | "srt_live";
export type SeatStatusSourceState = "ready" | "cooldown" | "unknown";
export type SeatStatusSourceCause = "provider_access_restricted" | "source_unavailable" | null;

export interface SeatStatusSource {
  provider: SeatStatusProvider;
  source: SeatStatusSourceName;
  state: SeatStatusSourceState;
  cause: SeatStatusSourceCause;
  retryAfterSeconds: number | null;
}

const expectedSources: readonly Pick<SeatStatusSource, "provider" | "source">[] = [
  { provider: "korail", source: "korail_browser" },
  { provider: "srt", source: "srt_live" },
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function retryAfterSeconds(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? Math.ceil(value)
    : null;
}

function unknownSource(expected: Pick<SeatStatusSource, "provider" | "source">): SeatStatusSource {
  return { ...expected, state: "unknown", cause: null, retryAfterSeconds: null };
}

function isExpectedSource(
  provider: unknown,
  source: unknown,
  expected: Pick<SeatStatusSource, "provider" | "source">,
): boolean {
  return provider === expected.provider && source === expected.source;
}

function mapExpectedSource(
  payload: unknown,
  expected: Pick<SeatStatusSource, "provider" | "source">,
): SeatStatusSource {
  if (!isRecord(payload) || !isExpectedSource(payload.provider, payload.source, expected)) {
    return unknownSource(expected);
  }

  if (payload.state === "ready" && payload.cause === null && payload.retry_after_seconds === null) {
    return { ...expected, state: "ready", cause: null, retryAfterSeconds: null };
  }

  const cause = payload.cause === "provider_access_restricted" || payload.cause === "source_unavailable"
    ? payload.cause
    : null;
  const retryAfter = retryAfterSeconds(payload.retry_after_seconds);
  if (payload.state === "cooldown" && cause !== null && retryAfter !== null) {
    return { ...expected, state: "cooldown", cause, retryAfterSeconds: retryAfter };
  }

  return unknownSource(expected);
}

export function mapSeatStatusSources(payload: unknown): SeatStatusSource[] {
  const items = Array.isArray(payload) ? payload : [];
  return expectedSources.map((expected) => {
    const matching = items.find((item) => isRecord(item) && isExpectedSource(item.provider, item.source, expected));
    return mapExpectedSource(matching, expected);
  });
}
