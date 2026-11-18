import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ServerSettings } from "../src/features/settings/ServerSettings";
import { APP_SCOPE_TEXT, WEB_SCOPE_TEXT } from "../src/features/settings/ServerSettingsFieldsForm";
import { VERSION_ERROR } from "../src/features/settings/serverSettingsForm";
import { SETTINGS_JOB_STORAGE_KEY } from "../src/features/settings/useServerSettings";
import {
  NEXT_SETTINGS_REVISION,
  SETTINGS_JOB_ID,
  SETTINGS_RAW,
  SETTINGS_REVISION,
  jsonResponse,
  routeFetch,
  settingsJobPayload,
  settingsPayload,
  unavailableSettingsPayload,
  type FetchRoute,
} from "./serverSettingsFixtures";

const accepted = { schema: 1, id: SETTINGS_JOB_ID, state: "queued" };
const APP_CONSENT = /앱 서비스를 다시 시작하는 동안 접속이 잠시 끊기는 데 동의합니다/;

function standardRoute(job: () => unknown = () => settingsJobPayload("succeeded", "succeeded")): FetchRoute {
  return ({ method, path }) => {
    if (method === "POST") return jsonResponse(accepted, 202);
    if (path.includes("/jobs/")) return jsonResponse(job());
    return jsonResponse(settingsPayload());
  };
}

async function renderLoaded(route: FetchRoute = standardRoute()) {
  const recorded = routeFetch(route);
  const user = userEvent.setup();
  render(<ServerSettings demo={false} />);
  const form = await screen.findByRole("form", { name: "서버 설정 항목" });
  return { user, form, ...recorded };
}

function describedText(element: HTMLElement): string {
  return (element.getAttribute("aria-describedby") ?? "")
    .split(" ")
    .map((id) => document.getElementById(id)?.textContent ?? "")
    .join(" ");
}

beforeEach(() => sessionStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe("server settings", () => {
  it("makes no requests in the demo", () => {
    const { spy } = routeFetch(() => jsonResponse(settingsPayload()));
    render(<ServerSettings demo />);
    expect(screen.getByRole("heading", { name: "서버 설정", level: 2 })).toBeTruthy();
    expect(screen.getByText("체험 화면에서는 서버 설정을 바꾸지 않습니다.")).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });

  it("groups labelled controls and never shows the stored key or the internal source name", async () => {
    const { form } = await renderLoaded();
    const reservations = within(screen.getByRole("group", { name: "자동 예매" }));
    expect((reservations.getByRole("switch", { name: "KTX·코레일 자동 예매" }) as HTMLInputElement).checked).toBe(false);
    expect((reservations.getByRole("switch", { name: "SRT 자동 예매" }) as HTMLInputElement).checked).toBe(true);

    const timetableKey = within(screen.getByRole("group", { name: "열차 시간표 인증키" }));
    expect(timetableKey.getByText("설정됨")).toBeTruthy();
    const keyInput = timetableKey.getByLabelText("새 인증키로 바꾸기") as HTMLInputElement;
    expect(keyInput.type).toBe("password");
    expect(keyInput.value).toBe("");
    expect(keyInput.getAttribute("autocomplete")).toBe("off");
    expect(keyInput.getAttribute("spellcheck")).toBe("false");

    const appLinks = within(screen.getByRole("group", { name: "Android 공식 앱 연결" }));
    for (const label of ["코레일+ 예매", "코레일+ 승차권", "SRT 예매 홈", "SRT 승차권 확인"]) {
      expect(appLinks.getByRole("switch", { name: `${label} 앱 연결` })).toBeTruthy();
      expect(appLinks.getByRole("textbox", { name: `${label} 검증한 앱 버전` })).toBeTruthy();
    }
    expect(form.textContent).not.toContain("TAGO");
    expect(screen.getByRole("heading", { name: "바뀐 항목 없음" })).toBeTruthy();
    expect((screen.getByRole("button", { name: "변경 적용" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("applies a web-only change without consent and asks for a page reload afterwards", async () => {
    let settingsReads = 0;
    const { user, requests } = await renderLoaded(({ method, path }) => {
      if (method === "POST") return jsonResponse(accepted, 202);
      if (path.includes("/jobs/")) return jsonResponse(settingsJobPayload("succeeded", "succeeded"));
      settingsReads += 1;
      return jsonResponse(settingsReads === 1
        ? settingsPayload()
        : settingsPayload({
          revision: NEXT_SETTINGS_REVISION,
          values: { VITE_SRT_MAIN_DEEPLINK_ENABLED: "true", VITE_SRT_MAIN_VALIDATED_VERSION: "2.0.41" },
        }));
    });
    const linkSwitch = screen.getByRole("switch", { name: "SRT 예매 홈 앱 연결" });
    await user.click(linkSwitch);
    expect(describedText(linkSwitch)).toContain("버전을 입력하기 전까지 이 연결은 꺼진 상태로 동작합니다.");
    await user.type(screen.getByRole("textbox", { name: "SRT 예매 홈 검증한 앱 버전" }), "2.0.41");
    expect(screen.queryByText("버전을 입력하기 전까지 이 연결은 꺼진 상태로 동작합니다.")).toBeNull();

    expect(screen.getByRole("heading", { name: "바뀐 항목 2개" })).toBeTruthy();
    expect(screen.getByText("SRT 예매 홈 앱 연결: 켬")).toBeTruthy();
    expect(screen.getByText(WEB_SCOPE_TEXT)).toBeTruthy();
    expect(screen.queryByRole("checkbox", { name: APP_CONSENT })).toBeNull();
    await user.click(screen.getByRole("button", { name: "변경 적용" }));

    expect(await screen.findByText("서버 설정을 적용했습니다. 바뀐 앱 연결 설정을 쓰려면 이 페이지를 새로고침하세요.")).toBeTruthy();
    const posts = requests.filter(({ method }) => method === "POST");
    expect(posts).toHaveLength(1);
    expect(posts[0]?.body).toEqual({
      base_revision: SETTINGS_REVISION,
      idempotency_key: expect.stringMatching(/^[a-f0-9]{32}$/),
      fields: { VITE_SRT_MAIN_DEEPLINK_ENABLED: "true", VITE_SRT_MAIN_VALIDATED_VERSION: "2.0.41" },
    });
    // The reloaded snapshot replaces the form, so nothing is left to apply.
    await waitFor(() => expect(screen.getByRole("heading", { name: "바뀐 항목 없음" })).toBeTruthy());
    expect((screen.getByRole("switch", { name: "SRT 예매 홈 앱 연결" }) as HTMLInputElement).checked).toBe(true);
    expect(screen.getByRole("status").textContent).toContain("서버 설정을 적용했습니다.");
  });

  it("requires consent before an app service restart", async () => {
    const { user, requests } = await renderLoaded();
    await user.click(screen.getByRole("switch", { name: "KTX·코레일 자동 예매" }));
    expect(screen.getByText(APP_SCOPE_TEXT)).toBeTruthy();
    const apply = screen.getByRole("button", { name: "변경 적용" }) as HTMLButtonElement;
    expect(apply.disabled).toBe(true);
    await user.click(screen.getByRole("checkbox", { name: APP_CONSENT }));
    expect(apply.disabled).toBe(false);
    await user.click(apply);
    await waitFor(() => expect(requests.filter(({ method }) => method === "POST")).toHaveLength(1));
    expect(requests.find(({ method }) => method === "POST")?.body).toMatchObject({
      fields: { KORAIL_RESERVATION_ONCE_ENABLED: "true" },
    });
  });

  it("blocks invalid values with an error tied to the input", async () => {
    const { user } = await renderLoaded();
    const version = screen.getByRole("textbox", { name: "SRT 승차권 확인 검증한 앱 버전" });
    await user.type(version, "2.0 beta");
    expect(version.getAttribute("aria-invalid")).toBe("true");
    expect(describedText(version)).toContain(VERSION_ERROR);
    const contact = screen.getByRole("textbox", { name: "발신자 연락처" });
    await user.type(contact, "https://example.com/push");
    expect(contact.getAttribute("aria-invalid")).toBe("true");
    expect((screen.getByRole("button", { name: "변경 적용" }) as HTMLButtonElement).disabled).toBe(true);
    await user.click(screen.getByRole("button", { name: "변경 취소" }));
    expect((version as HTMLInputElement).value).toBe("");
    expect(screen.getByRole("heading", { name: "바뀐 항목 없음" })).toBeTruthy();
  });

  it("stages replacing or clearing the stored key without echoing it", async () => {
    const { user, requests } = await renderLoaded();
    await user.type(screen.getByLabelText("새 인증키로 바꾸기"), "new-service-key%3D");
    expect(screen.getByText("공공데이터포털 열차정보 인증키: 새 키로 바꿈")).toBeTruthy();
    expect(document.body.textContent).not.toContain("new-service-key");
    await user.clear(screen.getByLabelText("새 인증키로 바꾸기"));

    await user.click(screen.getByRole("button", { name: "키 지우기" }));
    expect(screen.getByText("변경을 적용하면 저장한 인증키를 지웁니다.")).toBeTruthy();
    expect(screen.getByText("공공데이터포털 열차정보 인증키: 지움")).toBeTruthy();
    await user.click(screen.getByRole("checkbox", { name: APP_CONSENT }));
    await user.click(screen.getByRole("button", { name: "변경 적용" }));
    await waitFor(() => expect(requests.filter(({ method }) => method === "POST")).toHaveLength(1));
    expect(requests.find(({ method }) => method === "POST")?.body).toMatchObject({ fields: { TAGO_SERVICE_KEY: "" } });
  });

  it("submits the edited raw text from the advanced editor after consent", async () => {
    const { user, requests } = await renderLoaded();
    await user.click(screen.getByText("고급: .env 직접 편집"));
    const editor = screen.getByRole("textbox", { name: ".env 원문" }) as HTMLTextAreaElement;
    expect(editor.value).toBe(SETTINGS_RAW);
    expect(editor.getAttribute("wrap")).toBe("off");
    expect(editor.getAttribute("spellcheck")).toBe("false");
    expect(within(screen.getByRole("list", { name: "CLI로만 바꾸는 항목" })).getByText("COMPOSE_PROJECT_NAME")).toBeTruthy();
    const apply = screen.getByRole("button", { name: "원문 적용" }) as HTMLButtonElement;
    expect(apply.disabled).toBe(true);

    await user.type(editor, "VITE_SRT_TICKET_DEEPLINK_ENABLED=false");
    expect(apply.disabled).toBe(true);
    await user.click(screen.getByRole("checkbox", { name: /바뀐 항목에 따라 웹만 또는 앱 서비스 전체를 다시 시작합니다/ }));
    await user.click(apply);
    await waitFor(() => expect(requests.filter(({ method }) => method === "POST")).toHaveLength(1));
    expect(requests.find(({ method }) => method === "POST")?.body).toEqual({
      base_revision: SETTINGS_REVISION,
      idempotency_key: expect.stringMatching(/^[a-f0-9]{32}$/),
      raw: `${SETTINGS_RAW}VITE_SRT_TICKET_DEEPLINK_ENABLED=false`,
    });
    expect(await screen.findByText(/서버 설정을 적용했습니다. 바뀐 앱 연결 설정을 쓰려면/)).toBeTruthy();
  });

  it.each([
    [settingsJobPayload("failed", "rolled_back"), "적용에 실패해 이전 설정으로 되돌렸습니다."],
    [settingsJobPayload("rejected", "rejected", "E_SETTINGS_FORBIDDEN"), "CLI로만 바꿀 수 있는 항목이 바뀌어 적용하지 않았습니다."],
    [settingsJobPayload("deferred", "draining"), "진행 중인 작업이 있어 설정 적용을 보류했습니다."],
  ])("reports the finished job %#", async (job, message) => {
    const { user } = await renderLoaded(standardRoute(() => job));
    await user.click(screen.getByRole("switch", { name: "코레일+ 승차권 앱 연결" }));
    await user.click(screen.getByRole("button", { name: "변경 적용" }));
    expect(await screen.findByText(new RegExp(message))).toBeTruthy();
  });

  it("reloads and explains a revision conflict", async () => {
    let settingsReads = 0;
    const { user } = await renderLoaded(({ method }) => {
      if (method === "POST") return jsonResponse({ detail: "settings_changed" }, 409);
      settingsReads += 1;
      return jsonResponse(settingsPayload(settingsReads === 1
        ? {}
        : { revision: NEXT_SETTINGS_REVISION, values: { SRT_RESERVATION_ONCE_ENABLED: "false" } }));
    });
    await user.click(screen.getByRole("switch", { name: "코레일+ 승차권 앱 연결" }));
    await user.click(screen.getByRole("button", { name: "변경 적용" }));
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "다른 곳에서 설정이 바뀌었습니다. 새로 불러온 값을 확인한 뒤 다시 적용하세요.",
    );
    await waitFor(() => expect((screen.getByRole("switch", { name: "SRT 자동 예매" }) as HTMLInputElement).checked).toBe(false));
    expect((screen.getByRole("switch", { name: "코레일+ 승차권 앱 연결" }) as HTMLInputElement).checked).toBe(false);
  });

  it("keeps progress visible while the restarting API answers with maintenance", async () => {
    sessionStorage.setItem(SETTINGS_JOB_STORAGE_KEY, JSON.stringify({
      id: SETTINGS_JOB_ID,
      mode: "fields",
      scope: "app",
      reloadRequired: false,
    }));
    routeFetch(({ path }) => (path.includes("/jobs/")
      ? jsonResponse(settingsJobPayload("running", "backing_up"))
      : jsonResponse({ detail: "maintenance_in_progress" }, 503)));
    render(<ServerSettings demo={false} />);
    expect(await screen.findByText("서버 설정 적용 중: 암호화 백업 중")).toBeTruthy();
    expect(screen.getByText(`${APP_SCOPE_TEXT} 이 화면을 닫아도 적용은 계속됩니다.`)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps the loaded form when the snapshot reload after a job fails", async () => {
    let settingsReads = 0;
    const { user } = await renderLoaded(({ method, path }) => {
      if (method === "POST") return jsonResponse(accepted, 202);
      if (path.includes("/jobs/")) return jsonResponse(settingsJobPayload("succeeded", "succeeded"));
      settingsReads += 1;
      return settingsReads === 2
        ? jsonResponse({ detail: "maintenance_in_progress" }, 503)
        : jsonResponse(settingsPayload({ revision: settingsReads === 1 ? SETTINGS_REVISION : NEXT_SETTINGS_REVISION }));
    });
    await user.click(screen.getByRole("switch", { name: "코레일+ 예매 앱 연결" }));
    await user.click(screen.getByRole("button", { name: "변경 적용" }));
    expect(await screen.findByText("서버가 다시 시작되는 중입니다. 잠시 후 다시 불러와 주세요.")).toBeTruthy();
    expect(screen.getByRole("form", { name: "서버 설정 항목" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "다시 불러오기" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(settingsReads).toBe(3);
  });
});

describe("server settings availability", () => {
  it.each([
    ["managed_install_required", "직접 고친 뒤 서비스를 다시 시작하세요"],
    ["manager_unavailable", "설치한 컴퓨터의 관리 서비스와 Docker 실행 상태를 확인해 주세요."],
  ])("explains %s without a form", async (reason, message) => {
    routeFetch(() => jsonResponse(unavailableSettingsPayload(reason, reason !== "managed_install_required")));
    render(<ServerSettings demo={false} />);
    expect(await screen.findByText(new RegExp(message))).toBeTruthy();
    expect(screen.queryByRole("form")).toBeNull();
    expect(screen.getByRole("button", { name: "다시 확인" })).toBeTruthy();
  });

  it("guides a one-time manager replacement with copyable installer commands", async () => {
    routeFetch(() => jsonResponse(unavailableSettingsPayload("manager_upgrade_required")));
    const user = userEvent.setup();
    render(<ServerSettings demo={false} />);
    expect(await screen.findByText(/관리 서비스를 한 번 교체해야 합니다/)).toBeTruthy();
    expect(screen.getByText(/데이터와 설정은 그대로 두고/)).toBeTruthy();
    const unix = "curl -fsSL https://github.com/hostkimjang/korail-auto-waitlist/releases/latest/download/install.sh | sh";
    const windows = "irm https://github.com/hostkimjang/korail-auto-waitlist/releases/latest/download/install.ps1 | iex";
    expect(screen.getByText(unix).tagName).toBe("CODE");
    expect(screen.getByText(windows).tagName).toBe("CODE");
    expect(screen.getByText("railwait manager upgrade --path <설치 폴더>")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Windows PowerShell 명령 복사" }));
    expect(await navigator.clipboard.readText()).toBe(windows);
    expect(screen.getByText("복사했습니다.")).toBeTruthy();
  });

  it("shows the same guidance when a submission finds an outdated manager", async () => {
    const { user } = await renderLoaded(({ method }) => (method === "POST"
      ? jsonResponse({ detail: "settings_manager_upgrade_required" }, 409)
      : jsonResponse(settingsPayload())));
    await user.click(screen.getByRole("switch", { name: "SRT 승차권 확인 앱 연결" }));
    await user.click(screen.getByRole("button", { name: "변경 적용" }));
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "서버 설정을 바꾸려면 설치한 컴퓨터의 관리 서비스를 한 번 교체해야 합니다.",
    );
    expect(screen.getByRole("button", { name: "macOS·Linux 명령 복사" })).toBeTruthy();
  });
});
