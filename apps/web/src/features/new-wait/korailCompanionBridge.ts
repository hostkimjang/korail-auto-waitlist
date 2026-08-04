export type KorailCompanionFailureCode =
  | "bridge_not_paired"
  | "request_origin_mismatch"
  | "official_tab_missing"
  | "multiple_official_tabs"
  | "blocked"
  | "unsupported_page"
  | "passenger_unverified"
  | "parse_failed"
  | "bridge_reconnect_required"
  | "snapshot_rejected"
  | "extension_unavailable";

export type KorailCompanionImportResult =
  | {
    ok: true;
    origin: string;
    destination: string;
    travel_date: string;
    train_count: number;
  }
  | { ok: false; code: KorailCompanionFailureCode; status?: number };

const REQUEST_TYPE = "RAILWAIT_KORAIL_IMPORT_REQUEST";
const RESPONSE_TYPE = "RAILWAIT_KORAIL_IMPORT_RESPONSE";

export function requestKorailCompanionImport(
  timeoutMs = 8_000,
): Promise<KorailCompanionImportResult> {
  const requestId = crypto.randomUUID();
  return new Promise((resolve) => {
    const timeout = window.setTimeout(() => {
      window.removeEventListener("message", onMessage);
      resolve({ ok: false, code: "extension_unavailable" });
    }, timeoutMs);

    function onMessage(event: MessageEvent<unknown>): void {
      const result = readResponse(event, requestId);
      if (result === null) {
        return;
      }
      window.clearTimeout(timeout);
      window.removeEventListener("message", onMessage);
      resolve(result);
    }

    window.addEventListener("message", onMessage);
    window.postMessage({ type: REQUEST_TYPE, requestId }, window.location.origin);
  });
}

export function readResponse(
  event: MessageEvent<unknown>,
  requestId: string,
): KorailCompanionImportResult | null {
  if (
    event.source !== window ||
    event.origin !== window.location.origin ||
    !isRecord(event.data) ||
    event.data.type !== RESPONSE_TYPE ||
    event.data.requestId !== requestId
  ) {
    return null;
  }
  return normalizeImportResult(event.data.result);
}

function normalizeImportResult(value: unknown): KorailCompanionImportResult | null {
  if (!isRecord(value) || typeof value.ok !== "boolean") {
    return null;
  }
  if (value.ok) {
    if (
      typeof value.origin !== "string" ||
      typeof value.destination !== "string" ||
      typeof value.travel_date !== "string" ||
      typeof value.train_count !== "number" ||
      !Number.isInteger(value.train_count) ||
      value.train_count < 0
    ) {
      return null;
    }
    return {
      ok: true,
      origin: value.origin,
      destination: value.destination,
      travel_date: value.travel_date,
      train_count: value.train_count,
    };
  }
  if (!isFailureCode(value.code)) {
    return null;
  }
  return typeof value.status === "number"
    ? { ok: false, code: value.code, status: value.status }
    : { ok: false, code: value.code };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isFailureCode(value: unknown): value is KorailCompanionFailureCode {
  return typeof value === "string" && [
    "bridge_not_paired",
    "request_origin_mismatch",
    "official_tab_missing",
    "multiple_official_tabs",
    "blocked",
    "unsupported_page",
    "passenger_unverified",
    "parse_failed",
    "bridge_reconnect_required",
    "snapshot_rejected",
    "extension_unavailable",
  ].includes(value);
}
