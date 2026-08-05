export type SeatClassId = "standard" | "first";

export type NormalizedSeatProvenance = Record<string, unknown>;

export interface NormalizedSeatAction extends Record<string, unknown> {
  kind: string;
  url: string | null;
}

export interface NormalizedSeatClass extends Record<string, unknown> {
  seat_class: SeatClassId;
  status: string;
  fare: number | null;
  fare_currency: "KRW";
  provenance: NormalizedSeatProvenance;
  registration_evidence_id: string | null;
  registration_evidence_error: string | null;
  actions: NormalizedSeatAction[];
}
