import { ApiError } from "../../api/client";

// Wording for one kind of host-management request, e.g. the app update or a settings change.
export interface ManagementRequestWording {
  /** Noun phrase used in rejection sentences, such as "업데이트 요청". */
  request: string;
  /** Closing instruction after a new login, such as "업데이트해 주세요.". */
  retryAfterLogin: string;
}

export function serverDetail(payload: unknown): string | null {
  if (typeof payload !== "object" || payload === null || !("detail" in payload)) return null;
  return typeof payload.detail === "string" ? payload.detail : null;
}

function endsWithFinalConsonant(phrase: string): boolean {
  const last = phrase.charCodeAt(phrase.length - 1);
  return last >= 0xac00 && last <= 0xd7a3 && (last - 0xac00) % 28 !== 0;
}

// The API rejects state changes from an origin it does not trust and from an expired CSRF token
// with 403. Each needs a different user action, so name it instead of a generic failure.
export function managementAuthorizationError(
  cause: unknown,
  wording: ManagementRequestWording,
): string | null {
  if (!(cause instanceof ApiError)) return null;
  if (cause.status === 401) return `로그인이 만료되었습니다. 다시 로그인한 뒤 ${wording.retryAfterLogin}`;
  if (cause.status !== 403) return null;
  const detail = serverDetail(cause.detail);
  if (detail === "untrusted or missing Origin") {
    const subject = endsWithFinalConsonant(wording.request) ? "이" : "가";
    return `지금 접속한 주소는 서버가 허용한 접속 주소가 아니어서 ${wording.request}${subject} 거부되었습니다. 이 컴퓨터에서는 로컬 주소(127.0.0.1)로, 다른 기기에서는 원격 접속(HTTPS) 주소로 접속해 다시 실행해 주세요.`;
  }
  if (detail === "CSRF validation failed") return "보안 확인 값이 만료되었습니다. 화면을 새로고침한 뒤 다시 실행해 주세요.";
  const object = endsWithFinalConsonant(wording.request) ? "을" : "를";
  return `서버가 ${wording.request}${object} 거부했습니다. 다시 로그인한 뒤 실행해 주세요.`;
}
