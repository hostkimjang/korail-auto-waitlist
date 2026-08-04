import { expect, test, type Page, type Route } from "@playwright/test";

import {
  liveSmokeAuthenticationFailureMessage,
  resolveLiveSmokeAuthentication,
} from "./support/liveSmokeAuthentication";

type AuthFixtureMode = "login" | "initial_registration";

interface AuthFixtureState {
  authenticated: boolean;
  loginRequests: number;
  timetableRequests: number;
}

const syntheticUsername = "live-smoke-contract";
const syntheticPassword = "synthetic-e2e-password";

function json(route: Route, payload: unknown, status = 200): Promise<void> {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(payload),
  });
}

async function installAuthFixture(
  page: Page,
  mode: AuthFixtureMode,
): Promise<AuthFixtureState> {
  const state: AuthFixtureState = {
    authenticated: false,
    loginRequests: 0,
    timetableRequests: 0,
  };
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/events")) {
      await route.fulfill({ status: 204, body: "" });
      return;
    }
    if (path.endsWith("/auth/status")) {
      await json(route, {
        configured: mode === "login",
        authenticated: state.authenticated,
        registration_allowed: mode === "initial_registration",
      });
      return;
    }
    if (path.endsWith("/auth/login") && request.method() === "POST") {
      state.loginRequests += 1;
      const body: unknown = request.postDataJSON();
      if (
        typeof body === "object"
        && body !== null
        && "username" in body
        && "password" in body
        && body.username === syntheticUsername
        && body.password === syntheticPassword
      ) {
        state.authenticated = true;
        await json(route, { authenticated: true, expires_at: "2099-01-01T00:00:00Z" });
        return;
      }
      await json(route, { detail: "invalid username or password" }, 401);
      return;
    }
    if (path.endsWith("/timetables")) {
      state.timetableRequests += 1;
      await json(route, []);
      return;
    }
    if (path.endsWith("/watches") || path.endsWith("/notifications/channels")) {
      await json(route, []);
      return;
    }
    await json(route, []);
  });
  return state;
}

function runtimeAuthentication() {
  return resolveLiveSmokeAuthentication(
    {
      E2E_ADMIN_USERNAME: syntheticUsername,
      E2E_ADMIN_PASSWORD: syntheticPassword,
    },
    { repositoryRoot: process.cwd() },
  );
}

test("런타임 관리자 자격증명은 UI 로그인 한 번 뒤 홈에 진입하고 시간표를 호출하지 않는다", async ({ page }) => {
  const state = await installAuthFixture(page, "login");
  const authentication = runtimeAuthentication();
  expect(authentication.ready).toBe(true);
  if (!authentication.ready) throw new Error("synthetic authentication must be ready");

  await authentication.authenticate(page);
  await expect(page.getByRole("heading", { name: "활동 중인 대기" })).toBeVisible();
  expect(state).toMatchObject({ loginRequests: 1, timetableRequests: 0 });

  await authentication.authenticate(page);
  expect(state).toMatchObject({ loginRequests: 1, timetableRequests: 0 });
});

test("초기 관리자 미등록 상태에서는 계정을 만들거나 시간표를 호출하지 않고 중단한다", async ({ page }) => {
  const state = await installAuthFixture(page, "initial_registration");
  const authentication = runtimeAuthentication();
  expect(authentication.ready).toBe(true);
  if (!authentication.ready) throw new Error("synthetic authentication must be ready");

  let sanitizedMessage = "";
  try {
    await authentication.authenticate(page);
  } catch (error: unknown) {
    sanitizedMessage = liveSmokeAuthenticationFailureMessage(error);
  }

  expect(sanitizedMessage).toBe("관리자 로그인 화면의 상태를 확인할 수 없습니다.");
  expect(sanitizedMessage).not.toContain(syntheticUsername);
  expect(sanitizedMessage).not.toContain(syntheticPassword);
  expect(state).toMatchObject({ loginRequests: 0, timetableRequests: 0 });
  await expect(page.getByRole("heading", { name: "초기 관리자 등록" })).toBeVisible();
});
