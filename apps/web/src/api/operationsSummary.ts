import { mapOperationsSummary, type OperationsSummary } from "./operationsSummaryContract";

export class OperationsSummaryRequestError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "OperationsSummaryRequestError";
    this.status = status;
  }
}

export async function fetchOperationsSummary(signal?: AbortSignal): Promise<OperationsSummary> {
  const options: RequestInit = {
    headers: { Accept: "application/json" },
    credentials: "include",
    ...(signal ? { signal } : {}),
  };
  const response = await fetch("/api/v1/operations/summary", options);
  if (!response.ok) {
    throw new OperationsSummaryRequestError("로그·진행 상태를 불러오지 못했습니다.", response.status);
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new OperationsSummaryRequestError("운영 상태 응답 형식을 확인할 수 없습니다.", response.status);
  }
  return mapOperationsSummary(payload);
}
