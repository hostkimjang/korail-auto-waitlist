import { loadBridgeSettings, postSnapshot } from "./bridge";
import type { ContentResult } from "./types";

const button = requiredElement<HTMLButtonElement>("#import-current-results");
const status = requiredElement<HTMLElement>("#status");
const optionsLink = requiredElement<HTMLAnchorElement>("#open-options");

optionsLink.addEventListener("click", (event) => {
  event.preventDefault();
  void chrome.runtime.openOptionsPage();
});

button.addEventListener("click", () => {
  void importCurrentResults();
});

async function importCurrentResults(): Promise<void> {
  setStatus("현재 화면을 확인하는 중입니다.");
  button.disabled = true;
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab?.id === undefined) {
      setStatus("KORAIL 승차권 검색 결과 페이지에서만 사용할 수 있습니다.", true);
      return;
    }
    const result = await sendReadRequest(tab.id);
    if (!result.ok) {
      setStatus(messageForReadFailure(result.code), true);
      return;
    }
    const settings = await loadBridgeSettings();
    if (settings === null) {
      setStatus(`${result.payload.trains.length}개 열차를 읽었습니다. 서비스 브리지를 설정하면 전송합니다.`);
      return;
    }
    const postResult = await postSnapshot(settings, result.payload);
    const reconnectRequired = !postResult.ok && [401, 403, 410].includes(postResult.status);
    setStatus(
      postResult.ok
        ? `${result.payload.trains.length}개 열차의 일반실·특실 결과를 로컬 서비스에 전송했습니다.`
        : reconnectRequired
          ? "브라우저 연결이 만료되었거나 해제되었습니다. 확장 설정에서 새 1회 연결 코드로 다시 연결해 주세요."
        : `레일웨잇 서비스가 결과를 받지 않았습니다 (HTTP ${postResult.status}).`,
      !postResult.ok,
    );
  } catch {
    setStatus("현재 결과를 가져오지 못했습니다. 공식 화면을 다시 확인해 주세요.", true);
  } finally {
    button.disabled = false;
  }
}

async function sendReadRequest(tabId: number): Promise<ContentResult> {
  try {
    return (await chrome.tabs.sendMessage(tabId, {
      type: "READ_CURRENT_KORAIL_RESULTS",
    })) as ContentResult;
  } catch {
    return { ok: false, code: "unsupported_page" };
  }
}

function messageForReadFailure(code: Exclude<ContentResult, { ok: true }> ["code"]): string {
  switch (code) {
    case "blocked":
      return "보호 또는 비정상 접근 안내가 보여 전송하지 않았습니다. 공식 화면에서 나중에 직접 확인해 주세요.";
    case "passenger_unverified":
      return "공식 화면에서 승객 1명을 확인할 수 없어 전송하지 않았습니다.";
    case "unsupported_page":
      return "지원하지 않는 KORAIL 페이지입니다.";
    case "parse_failed":
      return "현재 보이는 결과의 날짜·경로·좌석 등급을 모두 확인할 수 없어 전송하지 않았습니다.";
  }
}

function setStatus(message: string, isError = false): void {
  status.textContent = message;
  status.dataset.state = isError ? "error" : "info";
}

function requiredElement<ElementType extends Element>(selector: string): ElementType {
  const element = document.querySelector<ElementType>(selector);
  if (element === null) {
    throw new Error(`Missing required popup element: ${selector}`);
  }
  return element;
}
