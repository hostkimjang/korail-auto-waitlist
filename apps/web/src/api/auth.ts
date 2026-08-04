import { request } from "./client";

export { ApiError } from "./client";

export async function getAuthStatus(): Promise<unknown> {
  return request("/auth/status");
}

export async function registerAdmin(
  username: string,
  password: string,
): Promise<unknown> {
  return request("/auth/register", {
    method: "POST",
    body: JSON.stringify({ username, password }),
    skipCsrf: true,
  });
}

export async function loginWithPassword(
  username: string,
  password: string,
): Promise<unknown> {
  return request("/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
    skipCsrf: true,
  });
}

export async function logout(): Promise<unknown> {
  return request("/auth/logout", { method: "POST" });
}
