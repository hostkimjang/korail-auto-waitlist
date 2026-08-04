export type SeatStatus =
  | "available"
  | "limited"
  | "standing_plus_seat"
  | "sold_out"
  | "waitlist_available"
  | "not_offered";

export interface KorailSeatSnapshot {
  seatClass: "standard" | "first";
  status: SeatStatus;
}

export interface KorailRenderedResultInput {
  trainNumber: string;
  origin: string;
  destination: string;
  departureTime: string;
  standardText: string;
  firstText: string;
  standardClassNames: readonly string[];
  firstClassNames: readonly string[];
}

export interface KorailSnapshotPayload {
  origin: string;
  destination: string;
  travel_date: string;
  passenger_count: 1;
  trains: Array<{
    train_number: string;
    departure_at: string;
    standard: SeatStatus;
    first: SeatStatus;
  }>;
}

export interface BridgeSettings {
  serviceBaseUrl: string;
  bridgeToken: string;
  credentialId: string;
  clientId: string;
}

export type ContentResult =
  | { ok: true; payload: KorailSnapshotPayload }
  | { ok: false; code: "blocked" | "unsupported_page" | "passenger_unverified" | "parse_failed" };
