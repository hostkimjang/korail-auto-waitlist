import { loadBridgeSettings, postSnapshot } from "./bridge";
import type { ContentResult } from "./types";

type ImportFailureCode =
  | "bridge_not_paired"
  | "request_origin_mismatch"
  | "official_tab_missing"
  | "multiple_official_tabs"
  | "blocked"
  | "unsupported_page"
  | "passenger_unverified"
  | "parse_failed"
  | "bridge_reconnect_required"
  | "snapshot_rejected"
  | "extension_unavailable";

type ImportResult =
  | {
    ok: true;
    origin: string;
    destination: string;
    travel_date: string;
    train_count: number;
  }
  | { ok: false; code: ImportFailureCode; status?: number };

interface ImportSender {
  tab?: { url?: string | undefined } | undefined;
}

chrome.runtime.onMessage.addListener((message: unknown, sender, sendResponse) => {
  if (!isImportRequest(message)) {
    return;
  }
  void importCurrentResults(sender).then(sendResponse).catch(() => {
    sendResponse({ ok: false, code: "extension_unavailable" } satisfies ImportResult);
  });
  return true;
});

export async function importCurrentResults(sender: ImportSender): Promise<ImportResult> {
  const settings = await loadBridgeSettings();
  if (settings === null) {
    return { ok: false, code: "bridge_not_paired" };
  }
  const senderOrigin = safeOrigin(sender.tab?.url);
  if (senderOrigin === null || senderOrigin !== settings.serviceBaseUrl) {
    return { ok: false, code: "request_origin_mismatch" };
  }

  const tabs = await chrome.tabs.query({ url: "https://www.korail.com/ticket/search/list*" });
  const readableTabs = tabs.filter((tab) => tab.id !== undefined);
  if (readableTabs.length === 0) {
    return { ok: false, code: "official_tab_missing" };
  }
  if (readableTabs.length > 1) {
    return { ok: false, code: "multiple_official_tabs" };
  }

  const [readableTab] = readableTabs;
  if (readableTab === undefined || readableTab.id === undefined) {
    return { ok: false, code: "official_tab_missing" };
  }
  const contentResult = await readCurrentResults(readableTab.id);
  if (!contentResult.ok) {
    return { ok: false, code: contentResult.code };
  }
  const postResult = await postSnapshot(settings, contentResult.payload);
  if (!postResult.ok) {
    return {
      ok: false,
      code: [401, 403, 410].includes(postResult.status)
        ? "bridge_reconnect_required"
        : "snapshot_rejected",
      status: postResult.status,
    };
  }
  return {
    ok: true,
    origin: contentResult.payload.origin,
    destination: contentResult.payload.destination,
    travel_date: contentResult.payload.travel_date,
    train_count: contentResult.payload.trains.length,
  };
}

async function readCurrentResults(tabId: number): Promise<ContentResult> {
  try {
    return await chrome.tabs.sendMessage(tabId, {
      type: "READ_CURRENT_KORAIL_RESULTS",
    }) as ContentResult;
  } catch {
    return { ok: false, code: "unsupported_page" };
  }
}

function isImportRequest(value: unknown): value is { type: string; requestId: string } {
  return (
    typeof value === "object" &&
    value !== null &&
    "type" in value &&
    value.type === "IMPORT_KORAIL_RESULTS_FROM_APP" &&
    "requestId" in value &&
    typeof value.requestId === "string"
  );
}

function safeOrigin(value: string | undefined): string | null {
  if (value === undefined) {
    return null;
  }
  try {
    return new URL(value).origin;
  } catch {
    return null;
  }
}
