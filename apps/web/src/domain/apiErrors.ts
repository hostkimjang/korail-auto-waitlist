export type RegistrationEvidenceConflictReason = "expired";

export interface ApiErrorDescriptor {
  code: string | null;
  reason: string | null;
  message: string;
}

type ApiOperation = "watch.create" | "watch.start";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function stringField(value: Record<string, unknown>, field: string): string | null {
  const candidate = value[field];
  return typeof candidate === "string" && candidate.trim() ? candidate : null;
}

export function describeApiErrorPayload(payload: unknown): ApiErrorDescriptor {
  const payloadRecord = isRecord(payload) ? payload : null;
  const rawDetail = payloadRecord?.detail ?? payload;
  if (isRecord(rawDetail)) {
    return {
      code: stringField(rawDetail, "code"),
      reason: stringField(rawDetail, "reason"),
      message: stringField(rawDetail, "message") ?? "요청을 처리하지 못했습니다.",
    };
  }
  return {
    code: null,
    reason: null,
    message: typeof rawDetail === "string" && rawDetail.trim()
      ? rawDetail
      : "요청을 처리하지 못했습니다.",
  };
}

export function isExpiredWatchCreateConflict(error: unknown): boolean {
  if (!isRecord(error)) return false;
  const status = error.status;
  const operation = error.operation;
  const descriptor = describeApiErrorPayload(error.detail);
  return status === 409
    && operation === ("watch.create" satisfies ApiOperation)
    && descriptor.code === "registration_evidence_conflict"
    && descriptor.reason === ("expired" satisfies RegistrationEvidenceConflictReason);
}
