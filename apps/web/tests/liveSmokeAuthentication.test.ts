import { resolve as resolvePath } from "node:path";

import { describe, expect, it, vi } from "vitest";

const playwrightExpect = vi.hoisted(() => vi.fn(() => ({
  toBeVisible: vi.fn().mockResolvedValue(undefined),
  toContainText: vi.fn().mockResolvedValue(undefined),
})));

vi.mock("@playwright/test", () => ({ expect: playwrightExpect }));

import {
  liveSmokeAuthFailureMessage,
  liveSmokeAuthenticationFailureMessage,
  resolveLiveSmokeStorageStatePath,
  resolveLiveSmokeAuthentication,
  type LiveSmokeAuthEnvironment,
} from "../e2e/support/liveSmokeAuthentication";

const repositoryRoot = resolvePath(process.cwd(), "test-repository");
const externalStorageState = resolvePath(
  process.cwd(),
  "..",
  "runtime-secrets",
  "playwright-state.json",
);
const validStorageState = JSON.stringify({
  cookies: [{ name: "session", value: "redacted" }],
  origins: [],
});

function resolve(
  environment: LiveSmokeAuthEnvironment,
  files: Readonly<Record<string, string>> = {},
) {
  return resolveLiveSmokeAuthentication(environment, {
    repositoryRoot,
    fileAccess: {
      exists: (path) => Object.hasOwn(files, path),
      read: (path) => {
        const payload = files[path];
        if (payload === undefined) throw new Error("missing fixture");
        return payload;
      },
    },
  });
}

describe("live smoke authentication preflight", () => {
  it("uses valid storage state with credentials as an explicit stale-session fallback", () => {
    const result = resolve(
      {
        E2E_STORAGE_STATE: externalStorageState,
        E2E_ADMIN_USERNAME: "runtime-admin",
        E2E_ADMIN_PASSWORD: "runtime-password-value",
      },
      { [externalStorageState]: validStorageState },
    );

    expect(result.ready).toBe(true);
    if (!result.ready) throw new Error("expected ready authentication");
    expect(result.mode).toBe("storage_state_with_credentials");
    expect(JSON.stringify(result)).not.toContain("runtime-password-value");
    expect(JSON.stringify(result)).not.toContain("redacted");
  });

  it("does not bypass an invalid configured storage state with credentials", () => {
    const password = "runtime-password-value";
    const result = resolve({
      E2E_STORAGE_STATE: "relative-state.json",
      E2E_ADMIN_USERNAME: " runtime-admin ",
      E2E_ADMIN_PASSWORD: password,
    });

    expect(result).toEqual({
      ready: false,
      reasons: ["storage_state_path_not_absolute"],
    });
    expect(JSON.stringify(result)).not.toContain(password);
    expect(JSON.stringify(result)).not.toContain("runtime-admin");
  });

  it("passes only a validated external storage state path to Playwright config", () => {
    const options = {
      repositoryRoot,
      fileAccess: {
        exists: (path: string) => path === externalStorageState,
        read: () => validStorageState,
      },
    };
    expect(resolveLiveSmokeStorageStatePath(
      { E2E_STORAGE_STATE: externalStorageState },
      options,
    )).toBe(externalStorageState);
    expect(resolveLiveSmokeStorageStatePath(
      { E2E_STORAGE_STATE: "relative-state.json" },
      options,
    )).toBeUndefined();
    expect(resolveLiveSmokeStorageStatePath({}, options)).toBeUndefined();
  });

  it("uses credentials directly only when no storage state path was configured", () => {
    const result = resolve({
      E2E_ADMIN_USERNAME: " runtime-admin ",
      E2E_ADMIN_PASSWORD: "runtime-password-value",
    });

    expect(result.ready).toBe(true);
    if (!result.ready) throw new Error("expected ready authentication");
    expect(result.mode).toBe("admin_credentials");
  });

  it("reports only fixed sanitized reasons for unusable inputs", () => {
    const secretUsername = "invalid administrator";
    const secretPassword = "secret-value";
    const secretPath = "relative-secret-state.json";
    const result = resolve({
      E2E_STORAGE_STATE: secretPath,
      E2E_ADMIN_USERNAME: secretUsername,
      E2E_ADMIN_PASSWORD: secretPassword,
    });

    expect(result.ready).toBe(false);
    if (result.ready) throw new Error("expected failed authentication preflight");
    expect(result.reasons).toEqual(["storage_state_path_not_absolute"]);
    const message = liveSmokeAuthFailureMessage(result);
    expect(message).not.toContain(secretUsername);
    expect(message).not.toContain(secretPassword);
    expect(message).not.toContain(secretPath);
    expect(message).toContain("절대 경로");
    expect(message).not.toContain("관리자 ID 형식");
  });

  it("rejects storage state inside the repository or with an empty payload", () => {
    const insidePath = resolvePath(repositoryRoot, "private", "state.json");
    const inside = resolve(
      { E2E_STORAGE_STATE: insidePath },
      { [insidePath]: validStorageState },
    );
    expect(inside).toEqual({
      ready: false,
      reasons: ["storage_state_inside_repository"],
    });

    const empty = resolve(
      { E2E_STORAGE_STATE: externalStorageState },
      { [externalStorageState]: JSON.stringify({ cookies: [], origins: [] }) },
    );
    expect(empty).toEqual({
      ready: false,
      reasons: ["storage_state_payload_invalid"],
    });
  });

  it("distinguishes a missing source from an incomplete credential pair", () => {
    const missing = resolve({});
    expect(missing).toEqual({
      ready: false,
      reasons: ["authentication_not_configured"],
    });

    const incomplete = resolve({ E2E_ADMIN_USERNAME: "runtime-admin" });
    expect(incomplete).toEqual({
      ready: false,
      reasons: ["admin_credentials_incomplete"],
    });
  });

  it("never reflects unknown authentication errors into diagnostics", () => {
    const secret = "unexpected-secret-error";
    expect(liveSmokeAuthenticationFailureMessage(new Error(secret))).toBe(
      "운영 스모크 인증을 완료하지 못했습니다.",
    );
    expect(liveSmokeAuthenticationFailureMessage(new Error(secret))).not.toContain(secret);
  });

  it("preserves password whitespace and submits a stale storage-state fallback exactly once", async () => {
    const password = "  runtime password with spaces  ";
    const usernameInput = {
      fill: vi.fn().mockResolvedValue(undefined),
      isVisible: vi.fn().mockResolvedValue(true),
    };
    const passwordInput = {
      fill: vi.fn().mockResolvedValue(undefined),
      isVisible: vi.fn().mockResolvedValue(true),
    };
    const loginButton = { click: vi.fn().mockResolvedValue(undefined) };
    const applicationHeading = { isVisible: vi.fn().mockResolvedValue(false) };
    const loginHeading = { isVisible: vi.fn().mockResolvedValue(true) };
    const main = {};
    const page = {
      goto: vi.fn().mockResolvedValue(undefined),
      locator: vi.fn().mockReturnValue(main),
      getByLabel: vi.fn((name: string) => (
        name === "관리자 ID" ? usernameInput : passwordInput
      )),
      getByRole: vi.fn((role: string, options: { name: string }) => {
        if (role === "button") return loginButton;
        return options.name === "활동 중인 대기" ? applicationHeading : loginHeading;
      }),
    };
    const result = resolve(
      {
        E2E_STORAGE_STATE: externalStorageState,
        E2E_ADMIN_USERNAME: "runtime-admin",
        E2E_ADMIN_PASSWORD: password,
      },
      { [externalStorageState]: validStorageState },
    );

    expect(result.ready).toBe(true);
    if (!result.ready) throw new Error("expected ready authentication");
    await Reflect.apply(result.authenticate, result, [page]);

    expect(page.goto).toHaveBeenCalledTimes(1);
    expect(loginButton.click).toHaveBeenCalledTimes(1);
    expect(passwordInput.fill).toHaveBeenNthCalledWith(1, password);
    expect(passwordInput.fill).toHaveBeenNthCalledWith(2, "");
    expect(usernameInput.fill).toHaveBeenNthCalledWith(1, "runtime-admin");
    expect(usernameInput.fill).toHaveBeenNthCalledWith(2, "");
  });
});
