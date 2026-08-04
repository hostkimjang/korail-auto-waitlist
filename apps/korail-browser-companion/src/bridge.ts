import type { BridgeSettings, KorailSnapshotPayload } from "./types";

const SETTINGS_KEY = "bridgeSettings";
const MINIMUM_TOKEN_LENGTH = 48;
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function normalizeServiceBaseUrl(value: string): string | null {
  let serviceUrl: URL;
  try {
    serviceUrl = new URL(value.trim());
  } catch {
    return null;
  }
  if (
    !["http:", "https:"].includes(serviceUrl.protocol) ||
    (serviceUrl.protocol === "http:" &&
      !["localhost", "127.0.0.1", "[::1]"].includes(serviceUrl.hostname)) ||
    serviceUrl.username ||
    serviceUrl.password ||
    serviceUrl.search ||
    serviceUrl.hash ||
    serviceUrl.pathname !== "/"
  ) {
    return null;
  }
  return serviceUrl.origin;
}

export function normalizeBridgeSettings(value: BridgeSettings): BridgeSettings | null {
  const serviceBaseUrl = normalizeServiceBaseUrl(value.serviceBaseUrl);
  const bridgeToken = value.bridgeToken.trim();
  if (
    serviceBaseUrl === null ||
    bridgeToken.length < MINIMUM_TOKEN_LENGTH ||
    !UUID_V4.test(value.credentialId) ||
    !UUID_V4.test(value.clientId)
  ) {
    return null;
  }
  return {
    serviceBaseUrl,
    bridgeToken,
    credentialId: value.credentialId,
    clientId: value.clientId,
  };
}

export async function loadBridgeSettings(): Promise<BridgeSettings | null> {
  const stored = await chrome.storage.local.get(SETTINGS_KEY);
  const value = stored[SETTINGS_KEY];
  if (!isBridgeSettings(value)) {
    return null;
  }
  return normalizeBridgeSettings(value);
}

export async function pairBridge(
  serviceBaseUrlInput: string,
  pairingCodeInput: string,
): Promise<{ ok: true; settings: BridgeSettings } | { ok: false; status: number }> {
  const serviceBaseUrl = normalizeServiceBaseUrl(serviceBaseUrlInput);
  const pairingCode = pairingCodeInput.trim();
  if (serviceBaseUrl === null || pairingCode.length < 32) {
    return { ok: false, status: 0 };
  }
  if (!(await requestServicePermission(serviceBaseUrl))) {
    return { ok: false, status: 0 };
  }
  const clientId = crypto.randomUUID();
  const response = await fetch(`${serviceBaseUrl}/api/v1/browser-bridge/pair`, {
    method: "POST",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    },
    body: JSON.stringify({ pairing_code: pairingCode, client_id: clientId }),
  });
  if (!response.ok) {
    return { ok: false, status: response.status };
  }
  const result = (await response.json()) as {
    credential_id?: unknown;
    bridge_token?: unknown;
  };
  const settings = normalizeBridgeSettings({
    serviceBaseUrl,
    bridgeToken: typeof result.bridge_token === "string" ? result.bridge_token : "",
    credentialId: typeof result.credential_id === "string" ? result.credential_id : "",
    clientId,
  });
  if (settings === null) {
    return { ok: false, status: 502 };
  }
  await chrome.storage.local.set({ [SETTINGS_KEY]: settings });
  await configureRemoteAppBridge(settings.serviceBaseUrl);
  return { ok: true, settings };
}

export async function clearBridgeSettings(): Promise<void> {
  await chrome.storage.local.remove(SETTINGS_KEY);
  await unregisterRemoteAppBridge();
}

export async function postSnapshot(
  settings: BridgeSettings,
  payload: KorailSnapshotPayload,
): Promise<{ ok: true } | { ok: false; status: number }> {
  const body = JSON.stringify(payload);
  const bodySha256 = await sha256Hex(body);
  const headers = {
    "Content-Type": "application/json",
    "X-Rail-Bridge-Token": settings.bridgeToken,
    "X-Rail-Bridge-Client-Id": settings.clientId,
    "Cache-Control": "no-store",
  };
  const challengeResponse = await fetch(
    `${settings.serviceBaseUrl}/api/v1/browser-bridge/challenges`,
    {
      method: "POST",
      cache: "no-store",
      headers,
      body: JSON.stringify({ body_sha256: bodySha256 }),
    },
  );
  if (!challengeResponse.ok) {
    return { ok: false, status: challengeResponse.status };
  }
  const challengePayload = (await challengeResponse.json()) as { challenge?: unknown };
  if (typeof challengePayload.challenge !== "string") {
    return { ok: false, status: 502 };
  }
  const response = await fetch(
    `${settings.serviceBaseUrl}/api/v1/browser-bridge/korail-snapshots`,
    {
      method: "POST",
      cache: "no-store",
      headers: {
        ...headers,
        "X-Rail-Bridge-Challenge": challengePayload.challenge,
      },
      body,
    },
  );
  return response.ok ? { ok: true } : { ok: false, status: response.status };
}

async function requestServicePermission(serviceBaseUrl: string): Promise<boolean> {
  if (!serviceBaseUrl.startsWith("https://")) {
    return true;
  }
  return chrome.permissions.request({ origins: [`${serviceBaseUrl}/*`] });
}

const REMOTE_APP_BRIDGE_ID = "railwait-remote-app-bridge";

async function configureRemoteAppBridge(serviceBaseUrl: string): Promise<void> {
  await unregisterRemoteAppBridge();
  if (!serviceBaseUrl.startsWith("https://")) {
    return;
  }
  await chrome.scripting.registerContentScripts([{
    id: REMOTE_APP_BRIDGE_ID,
    matches: [`${serviceBaseUrl}/*`],
    js: ["app-bridge.js"],
    runAt: "document_start",
    persistAcrossSessions: true,
  }]);
}

async function unregisterRemoteAppBridge(): Promise<void> {
  try {
    await chrome.scripting.unregisterContentScripts({ ids: [REMOTE_APP_BRIDGE_ID] });
  } catch {
    // The first pairing has no dynamic script to remove.
  }
}

async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function isBridgeSettings(value: unknown): value is BridgeSettings {
  return (
    typeof value === "object" &&
    value !== null &&
    "serviceBaseUrl" in value &&
    "bridgeToken" in value &&
    "credentialId" in value &&
    "clientId" in value &&
    typeof value.serviceBaseUrl === "string" &&
    typeof value.bridgeToken === "string" &&
    typeof value.credentialId === "string" &&
    typeof value.clientId === "string"
  );
}
