import type { ProviderRuntimeStatus } from "../../api/providerRuntime";

export interface ProviderRuntimeStatusPresentation {
  label: string;
  tone: "ready" | "pending" | "warning" | "blocked";
}

export function providerRuntimeStatusPresentation(
  status: ProviderRuntimeStatus,
): ProviderRuntimeStatusPresentation {
  if (status.state === "ready" && status.locallyReusable) {
    return { label: "재사용 가능", tone: "ready" };
  }
  if (status.state === "authenticating") {
    return { label: "로그인 준비 중", tone: "pending" };
  }
  if (status.state === "auth_required") {
    return { label: "로그인 필요", tone: "warning" };
  }
  if (status.state === "blocked") {
    return { label: "운영사 제한", tone: "blocked" };
  }
  if (status.state === "cold") {
    return { label: "시작 전", tone: "pending" };
  }
  return { label: "재검증 예정", tone: "warning" };
}

function formatRoundedDuration(seconds: number): string {
  if (seconds < 60) return "1분 이내";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}분`;
  const hours = Math.round(seconds / 3_600);
  if (hours < 24) return `${hours}시간`;
  return `${Math.round(seconds / 86_400)}일`;
}

export function formatRuntimeVerifiedAge(seconds: number | null): string {
  if (seconds === null) return "확인 기록 없음";
  if (seconds < 60) return "방금";
  return `${formatRoundedDuration(seconds)} 전`;
}

export function formatRuntimeLocalReuseWindow(seconds: number | null, reusable: boolean): string {
  if (!reusable) return "사용 불가";
  if (seconds === null) return "남은 시간 확인 불가";
  if (seconds === 0) return "종료됨";
  return `약 ${formatRoundedDuration(seconds)} 남음`;
}
