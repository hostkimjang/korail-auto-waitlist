export type RecoverableSeatClass = "standard" | "first";

type RecoveryFailureReason =
  | "identity_changed"
  | "status_changed"
  | "not_observed"
  | "evidence_missing";

type RecoveryResult =
  | { ok: true; train: Record<string, unknown> }
  | { ok: false; reason: RecoveryFailureReason; message: string };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function canonicalTrainNumber(value: unknown): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  const normalized = value.trim().toUpperCase();
  return /^\d+$/.test(normalized) ? normalized.replace(/^0+/, "") || "0" : normalized;
}

function instant(value: unknown): number | null {
  if (typeof value !== "string") return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function seatFor(train: Record<string, unknown>, seatClass: RecoverableSeatClass): Record<string, unknown> | null {
  if (!Array.isArray(train.seat_classes)) return null;
  const seat = train.seat_classes.find((candidate: unknown) => (
    isRecord(candidate) && candidate.seat_class === seatClass
  ));
  return isRecord(seat) ? seat : null;
}

function hasAddToWatchAction(seat: Record<string, unknown>): boolean {
  return Array.isArray(seat.actions) && seat.actions.some((action: unknown) => (
    isRecord(action) && action.kind === "add_to_watch"
  ));
}

function hasOfficialObservation(seat: Record<string, unknown>): boolean {
  if (!isRecord(seat.provenance)) return false;
  const { kind, source, observed_at: observedAt, fresh_until: freshUntil } = seat.provenance;
  if (
    typeof source !== "string"
    || !source.trim()
    || typeof observedAt !== "string"
    || !Number.isFinite(Date.parse(observedAt))
  ) return false;
  if (kind === "official_provider") return true;
  const expectedSource = kind === "official_page_browser_companion"
    ? "korail-official-browser-companion"
    : kind === "user_confirmed_official_page"
      ? "official-page-user-confirmation"
      : null;
  return expectedSource !== null
    && source === expectedSource
    && typeof freshUntil === "string"
    && Number.isFinite(Date.parse(freshUntil))
    && Date.parse(freshUntil) > Date.now();
}

function failure(reason: RecoveryFailureReason, message: string): RecoveryResult {
  return { ok: false, reason, message };
}

export function recoverRefreshedRegistrationTrain(
  originalValue: unknown,
  refreshedValues: unknown,
  seatClass: RecoverableSeatClass,
): RecoveryResult {
  if (!isRecord(originalValue) || !Array.isArray(refreshedValues)) {
    return failure("identity_changed", "재조회한 열차 정보를 확인할 수 없어 등록하지 않았습니다. 시간표에서 다시 선택해 주세요.");
  }
  const provider = typeof originalValue.provider === "string" ? originalValue.provider.toUpperCase() : null;
  const trainNumber = canonicalTrainNumber(originalValue.train_number);
  const departureAt = instant(originalValue.departure_at);
  const originalSeat = seatFor(originalValue, seatClass);
  if (!provider || !trainNumber || departureAt === null || originalSeat === null) {
    return failure("identity_changed", "선택한 열차 식별정보를 확인할 수 없어 등록하지 않았습니다. 시간표에서 다시 선택해 주세요.");
  }

  const exactMatches = refreshedValues.filter((candidate: unknown) => {
    if (!isRecord(candidate)) return false;
    const candidateProvider = typeof candidate.provider === "string" ? candidate.provider.toUpperCase() : null;
    return candidateProvider === provider
      && canonicalTrainNumber(candidate.train_number) === trainNumber
      && instant(candidate.departure_at) === departureAt;
  });
  if (exactMatches.length !== 1 || !isRecord(exactMatches[0])) {
    return failure("identity_changed", "재조회한 열차가 기존 선택과 달라 등록하지 않았습니다. 최신 시간표에서 다시 선택해 주세요.");
  }

  const refreshedTrain = exactMatches[0];
  const refreshedSeat = seatFor(refreshedTrain, seatClass);
  if (refreshedSeat === null) {
    return failure("identity_changed", "재조회한 열차에서 선택 좌석 등급을 찾지 못해 등록하지 않았습니다. 최신 시간표에서 다시 선택해 주세요.");
  }
  if (
    typeof originalSeat.status !== "string"
    || typeof refreshedSeat.status !== "string"
    || refreshedSeat.status !== originalSeat.status
    || !hasAddToWatchAction(refreshedSeat)
  ) {
    return failure("status_changed", "재조회 중 좌석 상태가 바뀌어 등록하지 않았습니다. 최신 상태를 확인한 뒤 다시 선택해 주세요.");
  }
  if (refreshedSeat.status === "unknown" || !hasOfficialObservation(refreshedSeat)) {
    return failure("not_observed", "좌석 상태를 새로 확인하지 못해 등록하지 않았습니다. 잠시 후 다시 조회해 주세요.");
  }
  const previousEvidenceId = originalSeat.registration_evidence_id;
  const refreshedEvidenceId = refreshedSeat.registration_evidence_id;
  if (
    typeof refreshedEvidenceId !== "string"
    || !refreshedEvidenceId.trim()
    || refreshedEvidenceId === previousEvidenceId
  ) {
    return failure("evidence_missing", "새 등록 근거를 발급받지 못해 등록하지 않았습니다. 시간표를 다시 조회해 주세요.");
  }
  return { ok: true, train: refreshedTrain };
}
