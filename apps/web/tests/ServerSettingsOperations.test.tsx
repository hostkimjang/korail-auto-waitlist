import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { mapSystemSettings } from "../src/api/systemSettings";
import { ServerSettings } from "../src/features/settings/ServerSettings";
import {
  ANDROID_APP_LINK_EVIDENCE_SOURCE,
  appLinkEvidenceEntries,
} from "../src/features/settings/androidAppLinkEvidence";
import { installLocationCommands, posixQuote } from "../src/features/settings/settingsCommands";
import { parseSettingsJobContext, settingsJobOutcome } from "../src/features/settings/serverSettingsJob";
import { RESTART_UPGRADE_MESSAGE } from "../src/features/settings/useServerSettings";
import {
  SETTINGS_JOB_ID,
  jsonResponse,
  routeFetch,
  settingsJobPayload,
  settingsPayload,
  unavailableSettingsPayload,
  type FetchRoute,
} from "./serverSettingsFixtures";

const REPOSITORY_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const RESTART_CONSENT = /지금 앱 서비스를 다시 시작하고/;
const MAC_LOCATION = {
  install_path: "/Users/me/Library/Application Support/Railwait",
  env_path: "/Users/me/Library/Application Support/Railwait/.env",
  host_os: "darwin",
};

async function renderSettings(route: FetchRoute) {
  const recorded = routeFetch(route);
  const user = userEvent.setup();
  render(<ServerSettings demo={false} />);
  await screen.findByRole("form", { name: "서버 설정 항목" });
  return { user, ...recorded };
}

beforeEach(() => sessionStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe("settings response boundary", () => {
  it("reads the restart capability and the install location, tolerating older APIs", () => {
    const current = mapSystemSettings(settingsPayload({ restartAvailable: true, installLocation: MAC_LOCATION }));
    expect(current.available && current.restartAvailable).toBe(true);
    expect(current.available && current.installLocation).toEqual({
      installPath: MAC_LOCATION.install_path,
      envPath: MAC_LOCATION.env_path,
      hostOs: "darwin",
    });
    const { restart_available: _restart, install_location: _location, ...older } = settingsPayload();
    const legacy = mapSystemSettings(older);
    expect(legacy.available && legacy.restartAvailable).toBe(false);
    expect(legacy.available && legacy.installLocation).toBeNull();
  });

  it.each([
    { restart_available: "yes" },
    { install_location: { ...MAC_LOCATION, host_os: "plan9" } },
    { install_location: { ...MAC_LOCATION, env_path: "" } },
    { install_location: { ...MAC_LOCATION, install_path: "/Users/me\nrm" } },
  ])("rejects a malformed operations field %#", (override) => {
    expect(() => mapSystemSettings({ ...settingsPayload(), ...override })).toThrow("서버 설정 응답 형식");
  });
});

describe("install location", () => {
  it("quotes paths for the shell of the install computer", () => {
    expect(posixQuote("/Users/me/it's here")).toBe("'/Users/me/it'\\''s here'");
    const commands = (hostOs: "darwin" | "linux" | "windows" | null, installPath: string, envPath: string) =>
      installLocationCommands({ installPath, envPath, hostOs }).map(({ command }) => command);
    expect(commands("darwin", "/Users/me/App Data", "/Users/me/App Data/.env")).toEqual([
      "open -R '/Users/me/App Data/.env'",
      "railwait open --path '/Users/me/App Data'",
    ]);
    expect(commands("windows", "C:\\Users\\me\\Rail Wait", "C:\\Users\\me\\Rail Wait\\.env")).toEqual([
      'explorer.exe "C:\\Users\\me\\Rail Wait"',
      'notepad.exe "C:\\Users\\me\\Rail Wait\\.env"',
      'railwait open --path "C:\\Users\\me\\Rail Wait"',
    ]);
    expect(commands("linux", "/home/me/railwait", "/home/me/railwait/.env")).toEqual([
      "xdg-open '/home/me/railwait'",
      "nano '/home/me/railwait/.env'",
      "railwait open --path '/home/me/railwait'",
    ]);
    expect(commands(null, "/srv/railwait", "/srv/railwait/.env")).toEqual(["railwait open --path '/srv/railwait'"]);
  });

  it("shows where .env is when the manager reports it, with copyable commands", async () => {
    const { user } = await renderSettings(() => jsonResponse(settingsPayload({ installLocation: MAC_LOCATION })));
    const card = within(screen.getByRole("region", { name: "설정 파일 위치" }));
    expect(card.getByText(MAC_LOCATION.env_path).tagName).toBe("CODE");
    expect(card.getByText("open -R '/Users/me/Library/Application Support/Railwait/.env'")).toBeTruthy();
    expect(card.getByText(/숨김 파일을 기본으로 보여 주지 않습니다/)).toBeTruthy();
    await user.click(card.getByRole("button", { name: ".env 위치 복사" }));
    expect(await navigator.clipboard.readText()).toBe(MAC_LOCATION.env_path);
  });

  it("leaves the card out when the location is unknown", async () => {
    await renderSettings(() => jsonResponse(settingsPayload()));
    expect(screen.queryByRole("region", { name: "설정 파일 위치" })).toBeNull();
  });

  it("tells Git installs where .env is and how to apply a change", async () => {
    routeFetch(() => jsonResponse(unavailableSettingsPayload("managed_install_required", false, false)));
    render(<ServerSettings demo={false} />);
    expect(await screen.findByText("open -R .env")).toBeTruthy();
    expect(screen.getByText("notepad .env")).toBeTruthy();
    expect(screen.getByText("bash ./scripts/ops.sh experimental")).toBeTruthy();
    expect(screen.getByText("./scripts/ops.ps1 experimental")).toBeTruthy();
  });
});

describe("service restart", () => {
  it("needs consent, sends one restart request and follows the job to the end", async () => {
    const { user, requests } = await renderSettings(({ method, path: requestPath }) => {
      if (method === "POST") return jsonResponse({ schema: 1, id: SETTINGS_JOB_ID, state: "queued" }, 202);
      if (requestPath.includes("/jobs/")) return jsonResponse(settingsJobPayload("succeeded", "succeeded"));
      return jsonResponse(settingsPayload());
    });
    const panel = within(screen.getByRole("region", { name: "서비스 다시 시작" }));
    const button = panel.getByRole("button", { name: "앱 서비스 다시 시작" });
    expect(button).toHaveProperty("disabled", true);
    expect(panel.getByText(/KORAIL 좌석 관측이 멈출 수 있으니/)).toBeTruthy();
    await user.click(panel.getByRole("checkbox", { name: RESTART_CONSENT }));
    await user.click(button);
    expect(await screen.findByText("앱 서비스를 다시 시작했습니다.")).toBeTruthy();
    const posts = requests.filter(({ method }) => method === "POST");
    expect(posts).toHaveLength(1);
    expect(posts[0]?.path).toBe("/api/v1/system/restart");
    expect(posts[0]?.body).toEqual({ scope: "app", idempotency_key: expect.stringMatching(/^[a-f0-9]{32}$/) });
    expect(requests.some(({ path: requestPath }) => requestPath === `/api/v1/system/update/jobs/${SETTINGS_JOB_ID}`)).toBe(true);
  });

  it("offers a manager replacement instead of the button when the manager cannot restart", async () => {
    const { requests } = await renderSettings(() => jsonResponse(settingsPayload({ restartAvailable: false })));
    const panel = within(screen.getByRole("region", { name: "서비스 다시 시작" }));
    expect(panel.queryByRole("button", { name: "앱 서비스 다시 시작" })).toBeNull();
    expect(panel.getByText("지금 관리 서비스는 화면에서 다시 시작하기를 지원하지 않습니다")).toBeTruthy();
    expect(panel.getByRole("button", { name: "관리 서비스 교체 · macOS·Linux 명령 복사" })).toBeTruthy();
    expect(requests.every(({ method }) => method === "GET")).toBe(true);
  });

  it("explains a manager replacement when the API refuses the restart", async () => {
    const { user } = await renderSettings(({ method }) => (method === "POST"
      ? jsonResponse({ detail: "restart_manager_upgrade_required" }, 409)
      : jsonResponse(settingsPayload())));
    const panel = within(screen.getByRole("region", { name: "서비스 다시 시작" }));
    await user.click(panel.getByRole("checkbox", { name: RESTART_CONSENT }));
    await user.click(panel.getByRole("button", { name: "앱 서비스 다시 시작" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", RESTART_UPGRADE_MESSAGE);
  });

  it("describes restart progress and refusals in plain words", () => {
    const context = parseSettingsJobContext(
      JSON.stringify({ id: SETTINGS_JOB_ID, mode: "restart", scope: "app", reloadRequired: false }),
    );
    expect(context).not.toBeNull();
    if (context === null) return;
    const job = { id: SETTINGS_JOB_ID, state: "running" as const, stage: "applying", errorCode: null };
    expect(settingsJobOutcome(job, context).message).toBe("서비스 다시 시작 중: 앱 서비스를 다시 시작하는 중");
    expect(settingsJobOutcome({ ...job, state: "rejected", stage: "rejected", errorCode: "E_RESTART_ENV_INVALID" }, context))
      .toMatchObject({ tone: "error", command: `railwait status --job ${SETTINGS_JOB_ID}` });
    expect(parseSettingsJobContext(
      JSON.stringify({ id: SETTINGS_JOB_ID, mode: "restart", scope: "web", reloadRequired: false }),
    )).toBeNull();
  });
});

describe("Android app link evidence", () => {
  it("equals the conservative candidate recorded in the QA document", () => {
    const document = readFileSync(path.join(REPOSITORY_ROOT, ANDROID_APP_LINK_EVIDENCE_SOURCE), "utf8");
    const section = document.split("### 현재 증거에 따른 보수적 활성 후보")[1] ?? "";
    const block = /```dotenv\n([\s\S]*?)```/.exec(section)?.[1] ?? "";
    const documented = Object.fromEntries(block.trim().split("\n").map((line) => {
      const [key, value = ""] = line.split("=", 2);
      return [key, value];
    }));
    expect(Object.keys(documented)).toHaveLength(8);
    expect(Object.fromEntries(appLinkEvidenceEntries())).toEqual(documented);
  });

  it("fills the developer's evidence only on request and shows each path's status", async () => {
    const { user, requests } = await renderSettings(() => jsonResponse(settingsPayload()));
    const group = within(screen.getByRole("group", { name: "Android 공식 앱 연결" }));
    expect(group.getByText("지금 값은 개발자 확인 값과 다릅니다.")).toBeTruthy();
    expect(group.getByText("개발자 확인: 7.0.0+70000006에서 동작을 확인했습니다.")).toBeTruthy();
    expect(group.getByText(/개발자 확인 전: 앱이 이미 열려 있을 때/)).toBeTruthy();
    await user.click(group.getByRole("button", { name: "개발자 확인 값 채우기" }));
    expect((group.getByRole("switch", { name: "코레일+ 예매 앱 연결" }) as HTMLInputElement).checked).toBe(true);
    expect((group.getByRole("switch", { name: "SRT 승차권 확인 앱 연결" }) as HTMLInputElement).checked).toBe(true);
    expect((group.getByRole("switch", { name: "SRT 예매 홈 앱 연결" }) as HTMLInputElement).checked).toBe(false);
    expect(group.getByText("지금 값은 개발자 확인 값과 같습니다.")).toBeTruthy();
    expect(group.getByRole("button", { name: "개발자 확인 값 채우기" })).toHaveProperty("disabled", true);
    expect(screen.getByRole("heading", { name: "바뀐 항목 4개" })).toBeTruthy();
    expect(requests.every(({ method }) => method === "GET")).toBe(true);
  });
});

describe("notification sender contact", () => {
  it("explains what the value is and what an empty value means", async () => {
    await renderSettings(() => jsonResponse(settingsPayload()));
    const group = within(screen.getByRole("group", { name: "알림 발신자 연락처" }));
    expect(group.getByText(/알림을 받는 사람에게는 보이지 않고/)).toBeTruthy();
    expect(group.getByText("어떤 값을 넣나요?")).toBeTruthy();
    expect(group.getByText("mailto:admin@localhost")).toBeTruthy();
    expect(group.getByText(/이미 알림을 켠 기기는 다시 연결하지 않아도 됩니다/)).toBeTruthy();
  });
});
