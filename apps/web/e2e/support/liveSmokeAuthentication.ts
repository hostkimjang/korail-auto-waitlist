import { expect, type Locator, type Page } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import { isAbsolute, relative, resolve } from "node:path";

export type LiveSmokeAuthFailureReason =
  | "authentication_not_configured"
  | "admin_credentials_incomplete"
  | "admin_username_invalid"
  | "admin_password_invalid"
  | "storage_state_path_not_absolute"
  | "storage_state_inside_repository"
  | "storage_state_file_missing"
  | "storage_state_payload_invalid";

export type LiveSmokeAuthenticationFailureReason =
  | "admin_login_failed"
  | "authentication_screen_unavailable"
  | "storage_state_session_rejected";

export interface LiveSmokeAuthEnvironment {
  readonly E2E_STORAGE_STATE?: string | undefined;
  readonly E2E_ADMIN_USERNAME?: string | undefined;
  readonly E2E_ADMIN_PASSWORD?: string | undefined;
}

interface LiveSmokeAuthFileAccess {
  exists(path: string): boolean;
  read(path: string): string;
}

export interface ResolveLiveSmokeAuthenticationOptions {
  readonly repositoryRoot: string;
  readonly fileAccess?: LiveSmokeAuthFileAccess;
}

export type LiveSmokeAuthentication =
  | {
      readonly ready: true;
      readonly mode:
        | "storage_state"
        | "storage_state_with_credentials"
        | "admin_credentials";
      authenticate(page: Page): Promise<void>;
    }
  | {
      readonly ready: false;
      readonly reasons: readonly LiveSmokeAuthFailureReason[];
    };

const authFailureMessages: Record<LiveSmokeAuthFailureReason, string> = {
  authentication_not_configured:
    "E2E_STORAGE_STATE 또는 E2E_ADMIN_USERNAME과 E2E_ADMIN_PASSWORD를 설정해야 합니다.",
  admin_credentials_incomplete:
    "E2E_ADMIN_USERNAME과 E2E_ADMIN_PASSWORD는 반드시 함께 설정해야 합니다.",
  admin_username_invalid: "E2E 관리자 ID 형식이 유효하지 않습니다.",
  admin_password_invalid: "E2E 관리자 비밀번호 형식이 유효하지 않습니다.",
  storage_state_path_not_absolute: "E2E_STORAGE_STATE는 절대 경로여야 합니다.",
  storage_state_inside_repository: "인증 storage state는 저장소 밖에 두어야 합니다.",
  storage_state_file_missing: "인증 storage state 파일을 찾을 수 없습니다.",
  storage_state_payload_invalid: "인증 storage state 파일 구조가 유효하지 않습니다.",
};

const authenticationFailureMessages: Record<LiveSmokeAuthenticationFailureReason, string> = {
  admin_login_failed: "런타임 관리자 로그인에 실패했습니다.",
  authentication_screen_unavailable: "관리자 로그인 화면의 상태를 확인할 수 없습니다.",
  storage_state_session_rejected: "인증 storage state 세션이 유효하지 않습니다.",
};

const defaultFileAccess: LiveSmokeAuthFileAccess = {
  exists: existsSync,
  read: (path) => readFileSync(path, "utf-8"),
};

const adminUsernamePattern = /^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$/;

class LiveSmokeAuthenticationError extends Error {
  readonly reason: LiveSmokeAuthenticationFailureReason;

  constructor(reason: LiveSmokeAuthenticationFailureReason) {
    super(authenticationFailureMessages[reason]);
    this.name = "LiveSmokeAuthenticationError";
    this.reason = reason;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isPathInside(parentPath: string, candidatePath: string): boolean {
  const relativePath = relative(parentPath, candidatePath);
  return relativePath === ""
    || (!relativePath.startsWith("..\\")
      && !relativePath.startsWith("../")
      && relativePath !== ".."
      && !isAbsolute(relativePath));
}

function hasUsableStorageStatePayload(serialized: string): boolean {
  try {
    const payload: unknown = JSON.parse(serialized);
    if (!isRecord(payload)) return false;
    const { cookies, origins } = payload;
    return Array.isArray(cookies)
      && Array.isArray(origins)
      && (cookies.length > 0 || origins.length > 0);
  } catch {
    return false;
  }
}

function inspectStorageState(
  configuredValue: string | undefined,
  repositoryRoot: string,
  fileAccess: LiveSmokeAuthFileAccess,
): { ready: true } | { ready: false; reason: LiveSmokeAuthFailureReason } | null {
  const configuredPath = configuredValue?.trim();
  if (!configuredPath) return null;
  if (!isAbsolute(configuredPath)) {
    return { ready: false, reason: "storage_state_path_not_absolute" };
  }

  const storageStatePath = resolve(configuredPath);
  if (isPathInside(resolve(repositoryRoot), storageStatePath)) {
    return { ready: false, reason: "storage_state_inside_repository" };
  }
  if (!fileAccess.exists(storageStatePath)) {
    return { ready: false, reason: "storage_state_file_missing" };
  }

  try {
    if (!hasUsableStorageStatePayload(fileAccess.read(storageStatePath))) {
      return { ready: false, reason: "storage_state_payload_invalid" };
    }
  } catch {
    return { ready: false, reason: "storage_state_payload_invalid" };
  }
  return { ready: true };
}

function inspectAdminCredentials(environment: LiveSmokeAuthEnvironment):
  | { ready: true; username: string; password: string }
  | { ready: false; reason: LiveSmokeAuthFailureReason }
  | null {
  const configuredUsername = environment.E2E_ADMIN_USERNAME;
  const configuredPassword = environment.E2E_ADMIN_PASSWORD;
  const hasUsername = Boolean(configuredUsername?.trim());
  const hasPassword = Boolean(configuredPassword);
  if (!hasUsername && !hasPassword) return null;
  if (!hasUsername || !hasPassword) {
    return { ready: false, reason: "admin_credentials_incomplete" };
  }

  const username = configuredUsername?.trim() ?? "";
  const password = configuredPassword ?? "";
  if (!adminUsernamePattern.test(username)) {
    return { ready: false, reason: "admin_username_invalid" };
  }
  if (password.length > 128) {
    return { ready: false, reason: "admin_password_invalid" };
  }
  return { ready: true, username, password };
}

async function clearCredentialField(locator: Locator): Promise<void> {
  try {
    if (await locator.isVisible()) await locator.fill("");
  } catch {
    // The authenticated application replaces the login form, so a detached field is expected.
  }
}

async function waitForAuthenticationScreen(page: Page): Promise<"authenticated" | "login"> {
  const applicationHeading = page.getByRole("heading", { name: "활동 중인 대기" });
  const loginHeading = page.getByRole("heading", { name: "관리자 로그인" });
  await expect(page.locator("main")).toContainText(
    /활동 중인 대기|관리자 로그인|초기 관리자 등록|서버 연결을 확인해 주세요/,
    { timeout: 15_000 },
  );
  if (await applicationHeading.isVisible()) return "authenticated";
  if (await loginHeading.isVisible()) return "login";
  throw new LiveSmokeAuthenticationError("authentication_screen_unavailable");
}

async function authenticateWithStorageState(page: Page): Promise<void> {
  await page.goto("/");
  if (await waitForAuthenticationScreen(page) !== "authenticated") {
    throw new LiveSmokeAuthenticationError("storage_state_session_rejected");
  }
}

async function submitAdminLogin(
  page: Page,
  username: string,
  password: string,
): Promise<void> {
  const usernameInput = page.getByLabel("관리자 ID");
  const passwordInput = page.getByLabel("비밀번호", { exact: true });
  try {
    await usernameInput.fill(username);
    await passwordInput.fill(password);
    await page.getByRole("button", { name: "로그인", exact: true }).click();
    await expect(page.getByRole("heading", { name: "활동 중인 대기" })).toBeVisible({
      timeout: 15_000,
    });
  } catch {
    throw new LiveSmokeAuthenticationError("admin_login_failed");
  } finally {
    await Promise.all([
      clearCredentialField(usernameInput),
      clearCredentialField(passwordInput),
    ]);
  }
}

async function authenticateWithAdminCredentials(
  page: Page,
  username: string,
  password: string,
): Promise<void> {
  await page.goto("/");
  if (await waitForAuthenticationScreen(page) === "authenticated") return;
  await submitAdminLogin(page, username, password);
}

async function authenticateWithStorageStateOrAdminCredentials(
  page: Page,
  username: string,
  password: string,
): Promise<void> {
  await page.goto("/");
  if (await waitForAuthenticationScreen(page) === "authenticated") return;
  await submitAdminLogin(page, username, password);
}

export function liveSmokeAuthFailureMessage(
  failure: Pick<Extract<LiveSmokeAuthentication, { ready: false }>, "reasons">,
): string {
  return failure.reasons.map((reason) => authFailureMessages[reason]).join(" ");
}

export function liveSmokeAuthenticationFailureMessage(reason: unknown): string {
  return reason instanceof LiveSmokeAuthenticationError
    ? authenticationFailureMessages[reason.reason]
    : "운영 스모크 인증을 완료하지 못했습니다.";
}

export function resolveLiveSmokeStorageStatePath(
  environment: LiveSmokeAuthEnvironment,
  options: ResolveLiveSmokeAuthenticationOptions,
): string | undefined {
  const configuredPath = environment.E2E_STORAGE_STATE?.trim();
  if (!configuredPath) return undefined;
  const storageState = inspectStorageState(
    configuredPath,
    options.repositoryRoot,
    options.fileAccess ?? defaultFileAccess,
  );
  return storageState?.ready ? resolve(configuredPath) : undefined;
}

export function resolveLiveSmokeAuthentication(
  environment: LiveSmokeAuthEnvironment,
  options: ResolveLiveSmokeAuthenticationOptions,
): LiveSmokeAuthentication {
  const fileAccess = options.fileAccess ?? defaultFileAccess;
  const storageState = inspectStorageState(
    environment.E2E_STORAGE_STATE,
    options.repositoryRoot,
    fileAccess,
  );
  const credentials = inspectAdminCredentials(environment);

  // An explicitly configured storage-state path is a security boundary. A malformed or
  // misplaced file must be fixed instead of being silently ignored in favor of credentials.
  if (storageState && !storageState.ready) {
    return { ready: false, reasons: [storageState.reason] };
  }

  if (storageState?.ready && credentials && !credentials.ready) {
    return { ready: false, reasons: [credentials.reason] };
  }

  if (storageState?.ready && credentials?.ready) {
    return {
      ready: true,
      mode: "storage_state_with_credentials",
      authenticate: (page) => authenticateWithStorageStateOrAdminCredentials(
        page,
        credentials.username,
        credentials.password,
      ),
    };
  }

  if (storageState?.ready) {
    return {
      ready: true,
      mode: "storage_state",
      authenticate: authenticateWithStorageState,
    };
  }

  if (credentials?.ready) {
    return {
      ready: true,
      mode: "admin_credentials",
      authenticate: (page) => authenticateWithAdminCredentials(
        page,
        credentials.username,
        credentials.password,
      ),
    };
  }

  return {
    ready: false,
    reasons: [credentials?.reason ?? "authentication_not_configured"],
  };
}
