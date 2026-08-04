import type { SeatStatusSource } from "../api/seatStatusSourcesContract";

export const demoSeatStatusSources: SeatStatusSource[] = [
  { provider: "korail", source: "korail_browser", state: "cooldown", cause: "provider_access_restricted", retryAfterSeconds: 300 },
  { provider: "srt", source: "srt_live", state: "ready", cause: null, retryAfterSeconds: null },
];
