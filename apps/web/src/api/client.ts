import {
  describeApiErrorPayload,
  type ApiErrorDescriptor,
} from "../domain/apiErrors";

export const API_ROOT = "/api/v1";

interface ApiErrorContext {
  code?: string | null;
  reason?: string | null;
  operation?: string | null;
}

export class ApiError extends Error {
  status: number;
  detail: unknown;
  code: string | null;
  reason: string | null;
  operation: string | null;

  constructor(
    message: string,
    status = 0,
    detail: unknown = null,
    descriptor: ApiErrorContext = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.code = descriptor.code ?? null;
    this.reason = descriptor.reason ?? null;
    this.operation = descriptor.operation ?? null;
  }
}

export interface ApiRequestOptions extends RequestInit {
  skipCsrf?: boolean;
}

function cookie(name: string): string {
  const entry = document.cookie
    .split("; ")
    .find((value) => value.startsWith(`${name}=`));
  return entry ? decodeURIComponent(entry.slice(name.length + 1)) : "";
}

export async function request(
  path: string,
  options: ApiRequestOptions = {},
): Promise<unknown> {
  const method = options.method ?? "GET";
  const headers = new Headers(options.headers ?? {});
  headers.set("Accept", "application/json");
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase()) && !options.skipCsrf) {
    const token = cookie("rail_csrf");
    if (token) headers.set("X-CSRF-Token", token);
  }
  const response = await fetch(`${API_ROOT}${path}`, {
    ...options,
    method,
    headers,
    credentials: "include",
  });
  if (response.status === 204) return null;
  const type = response.headers.get("content-type") ?? "";
  const payload: unknown = type.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    const descriptor: ApiErrorDescriptor = describeApiErrorPayload(payload);
    throw new ApiError(descriptor.message, response.status, payload, descriptor);
  }
  return payload;
}
