import { request } from "./client";
import type { RailProvider } from "./providerAccounts";

/**
 * Whether this installation lets automatic reservation run for each official
 * provider. The server opens it only when its installation-wide reservation
 * switch is on; a watch policy or a signed-in account alone never does.
 */
export type AutomaticReservationAvailability = Readonly<Record<RailProvider, boolean>>;

const invalidCapabilities = "철도사 기능 응답 형식을 확인할 수 없습니다.";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function officialReservationOnce(payload: ReadonlyArray<unknown>, provider: "korail" | "srt"): boolean {
  const official = payload.filter((item) => isRecord(item)
    && item.provider === provider
    && item.timetable === true
    && item.experimental === false);
  const item = official[0];
  if (official.length !== 1 || !isRecord(item) || typeof item.reservation_once !== "boolean") {
    throw new Error(invalidCapabilities);
  }
  return item.reservation_once;
}

export function mapAutomaticReservationAvailability(
  payload: unknown,
): AutomaticReservationAvailability {
  if (!Array.isArray(payload)) throw new Error(invalidCapabilities);
  return {
    KORAIL: officialReservationOnce(payload, "korail"),
    SRT: officialReservationOnce(payload, "srt"),
  };
}

export async function fetchAutomaticReservationAvailability(): Promise<AutomaticReservationAvailability> {
  return mapAutomaticReservationAvailability(await request("/providers", { cache: "no-store" }));
}

/** Unknown (`null`) until the server answered; mock watches never reserve on a provider. */
export function automaticReservationAvailableFor(
  availability: AutomaticReservationAvailability | null,
  provider: string,
): boolean | null {
  if (availability === null) return null;
  if (provider === "KORAIL" || provider === "SRT") return availability[provider];
  return null;
}
