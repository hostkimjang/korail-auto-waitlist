import { clearBridgeSettings, loadBridgeSettings, pairBridge } from "./bridge";

const form = requiredElement<HTMLFormElement>("#bridge-settings");
const serviceBaseUrl = requiredElement<HTMLInputElement>("#service-base-url");
const pairingCode = requiredElement<HTMLInputElement>("#pairing-code");
const disconnectButton = requiredElement<HTMLButtonElement>("#disconnect");
const status = requiredElement<HTMLElement>("#status");

void restoreSettings();
form.addEventListener("submit", (event) => {
  event.preventDefault();
  void connect();
});
disconnectButton.addEventListener("click", () => {
  void disconnect();
});

async function restoreSettings(): Promise<void> {
  const settings = await loadBridgeSettings();
  if (settings === null) {
    disconnectButton.hidden = true;
    return;
  }
  serviceBaseUrl.value = settings.serviceBaseUrl;
  pairingCode.value = "";
  disconnectButton.hidden = false;
  setStatus("이 브라우저는 레일웨잇과 연결되어 있습니다.");
}

async function connect(): Promise<void> {
  setStatus("1회 연결 코드를 확인하는 중입니다.");
  const result = await pairBridge(serviceBaseUrl.value, pairingCode.value);
  pairingCode.value = "";
  if (!result.ok) {
    setStatus(
      result.status === 0
        ? "loopback HTTP 또는 HTTPS 주소와 1회 연결 코드를 확인해 주세요."
        : `연결 코드를 사용할 수 없습니다 (HTTP ${result.status}). 새 코드를 발급해 주세요.`,
      true,
    );
    return;
  }
  serviceBaseUrl.value = result.settings.serviceBaseUrl;
  disconnectButton.hidden = false;
  setStatus("연결했습니다. 이후 결과 가져오기에서는 자격증명을 자동으로 사용합니다.");
}

async function disconnect(): Promise<void> {
  await clearBridgeSettings();
  disconnectButton.hidden = true;
  pairingCode.value = "";
  setStatus("이 브라우저의 연결 정보를 지웠습니다. 서버 설정에서도 연결을 해제할 수 있습니다.");
}

function setStatus(message: string, error = false): void {
  status.textContent = message;
  status.dataset.state = error ? "error" : "info";
}

function requiredElement<ElementType extends Element>(selector: string): ElementType {
  const element = document.querySelector<ElementType>(selector);
  if (element === null) {
    throw new Error(`Missing required options element: ${selector}`);
  }
  return element;
}
