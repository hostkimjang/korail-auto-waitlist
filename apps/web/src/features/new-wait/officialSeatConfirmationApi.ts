export type OfficialProvider = "KORAIL" | "SRT";
export type ConfirmableSeatClass = "standard" | "first";
export type OfficialSeatConfirmationStatus =
  | "available"
  | "sold_out"
  | "waitlist_available"
  | "not_offered";

export interface OfficialSeatConfirmationItemInput {
  seat_class: ConfirmableSeatClass;
  status: OfficialSeatConfirmationStatus;
}

export interface OfficialSeatConfirmationInput {
  provider: OfficialProvider;
  origin_node_id: string;
  destination_node_id: string;
  train_number: string;
  departure_at: string;
  passenger_count: number;
  seat_classes: OfficialSeatConfirmationItemInput[];
}

export interface OfficialSeatConfirmationItem {
  id: string;
  seatClass: ConfirmableSeatClass;
  status: OfficialSeatConfirmationStatus;
}

export interface OfficialSeatConfirmationResult {
  provider: OfficialProvider;
  originNodeId: string;
  destinationNodeId: string;
  trainNumber: string;
  departureAt: string;
  passengerCount: number;
  seatClasses: OfficialSeatConfirmationItem[];
  source: "official-page-user-confirmation";
  provenanceKind: "user_confirmed_official_page";
  observedAt: string;
  freshUntil: string;
  receivedAtMonotonicMs: number;
  freshnessTtlMs: number;
  createdCount: number;
  replayed: boolean;
}

const CONFIRMATION_PATH = "/api/v1/seat-observations/official-page-confirmations";
const SOURCE = "official-page-user-confirmation";
const PROVENANCE_KIND = "user_confirmed_official_page";
const MAX_CONFIRMATION_TTL_MS = 5 * 60 * 1000;
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isAwareDateTime(value: unknown): value is string {
  return typeof value === "string"
    && /(?:Z|[+-]\d{2}:\d{2})$/i.test(value)
    && Number.isFinite(Date.parse(value));
}

function normalizedProvider(value: unknown): OfficialProvider | null {
  if (typeof value !== "string") return null;
  const provider = value.toUpperCase();
  return provider === "KORAIL" || provider === "SRT" ? provider : null;
}

function normalizedSeatClass(value: unknown): ConfirmableSeatClass | null {
  return value === "standard" || value === "first" ? value : null;
}

function normalizedStatus(value: unknown): OfficialSeatConfirmationStatus | null {
  if (
    value === "available"
    || value === "sold_out"
    || value === "waitlist_available"
    || value === "not_offered"
  ) return value;
  return null;
}

function requiredString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function confirmationItem(value: unknown): OfficialSeatConfirmationItem | null {
  if (!isRecord(value)) return null;
  const id = requiredString(value.id);
  const seatClass = normalizedSeatClass(value.seat_class);
  const status = normalizedStatus(value.status);
  if (!id || !seatClass || !status) return null;
  return { id, seatClass, status };
}

export function mapOfficialSeatConfirmationResponse(value: unknown): OfficialSeatConfirmationResult {
  if (!isRecord(value)) throw new Error("공식 좌석 확인 저장 응답 형식이 올바르지 않습니다.");
  const provider = normalizedProvider(value.provider);
  const originNodeId = requiredString(value.origin_node_id);
  const destinationNodeId = requiredString(value.destination_node_id);
  const trainNumber = requiredString(value.train_number);
  const items = Array.isArray(value.seat_classes)
    ? value.seat_classes.map(confirmationItem)
    : [];
  const validItems = items.filter((item): item is OfficialSeatConfirmationItem => item !== null);
  const uniqueClasses = new Set(validItems.map((item) => item.seatClass));
  const receivedAtMonotonicMs = typeof performance !== "undefined" && typeof performance.now === "function"
    ? performance.now()
    : Number.NaN;
  const freshnessTtlMs = isAwareDateTime(value.observed_at) && isAwareDateTime(value.fresh_until)
    ? Date.parse(value.fresh_until) - Date.parse(value.observed_at)
    : Number.NaN;
  if (
    !provider
    || !originNodeId
    || !destinationNodeId
    || !trainNumber
    || !isAwareDateTime(value.departure_at)
    || typeof value.passenger_count !== "number"
    || !Number.isInteger(value.passenger_count)
    || value.passenger_count < 1
    || value.passenger_count > 9
    || validItems.length === 0
    || validItems.length !== items.length
    || uniqueClasses.size !== validItems.length
    || value.source !== SOURCE
    || value.provenance_kind !== PROVENANCE_KIND
    || !isAwareDateTime(value.observed_at)
    || !isAwareDateTime(value.fresh_until)
    || !Number.isFinite(freshnessTtlMs)
    || freshnessTtlMs <= 0
    || freshnessTtlMs > MAX_CONFIRMATION_TTL_MS
    || !Number.isFinite(receivedAtMonotonicMs)
    || receivedAtMonotonicMs < 0
    || typeof value.created_count !== "number"
    || !Number.isInteger(value.created_count)
    || value.created_count < 0
    || typeof value.replayed !== "boolean"
  ) throw new Error("공식 좌석 확인 저장 응답의 근거를 검증하지 못했습니다.");

  return {
    provider,
    originNodeId,
    destinationNodeId,
    trainNumber,
    departureAt: value.departure_at,
    passengerCount: value.passenger_count,
    seatClasses: validItems,
    source: SOURCE,
    provenanceKind: PROVENANCE_KIND,
    observedAt: value.observed_at,
    freshUntil: value.fresh_until,
    receivedAtMonotonicMs,
    freshnessTtlMs,
    createdCount: value.created_count,
    replayed: value.replayed,
  };
}

function csrfToken(): string {
  const entry = document.cookie
    .split("; ")
    .find((value) => value.startsWith("rail_csrf="));
  return entry ? decodeURIComponent(entry.slice("rail_csrf=".length)) : "";
}

function publicErrorMessage(payload: unknown): string {
  if (!isRecord(payload) || typeof payload.detail !== "string") {
    return "공식 좌석 확인 결과를 저장하지 못했습니다.";
  }
  if (payload.detail.includes("conflict") || payload.detail.includes("idempotent")) {
    return "저장 요청이 충돌했습니다. 좌석 상태를 다시 선택해 주세요.";
  }
  return "공식 좌석 확인 결과를 저장하지 못했습니다.";
}

export async function saveOfficialSeatConfirmation(
  input: OfficialSeatConfirmationInput,
  idempotencyKey: string,
): Promise<OfficialSeatConfirmationResult> {
  const headers = new Headers({
    Accept: "application/json",
    "Content-Type": "application/json",
    "Idempotency-Key": idempotencyKey,
  });
  const csrf = csrfToken();
  if (csrf) headers.set("X-CSRF-Token", csrf);
  const response = await fetch(CONFIRMATION_PATH, {
    method: "POST",
    headers,
    credentials: "include",
    body: JSON.stringify({
      ...input,
      provider: input.provider.toLowerCase(),
    }),
  });
  const contentType = response.headers.get("content-type") ?? "";
  const payload: unknown = contentType.includes("application/json")
    ? await response.json()
    : null;
  if (!response.ok) throw new Error(publicErrorMessage(payload));
  return mapOfficialSeatConfirmationResponse(payload);
}
