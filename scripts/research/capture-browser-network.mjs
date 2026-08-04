#!/usr/bin/env node

import { execFileSync } from "node:child_process";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

const DEFAULT_CDP_URL = "http://127.0.0.1:9222";
const DEFAULT_DURATION_MS = 15_000;
const DEFAULT_MAX_RECORDS = 2_000;
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]"]);

// 캡처 파일에 그대로 남겨도 되는 구조적 이름만 명시합니다. 새 이름은 자동 허용하지
// 않고 {segment}로 축약해 사용자명·세션값처럼 보이지 않는 식별자도 fail-closed 처리합니다.
const SAFE_PATH_SEGMENTS = new Set([
  "classes",
  "com",
  "com.korail.mobile.schedule.clearRunDt",
  "com.korail.mobile.schedule.runDt",
  "css",
  "dyna",
  "dynaPath",
  "dynaPath.do",
  "images",
  "js",
  "macro.do",
  "netfunnel",
  "netfunnel-pr.1.0.js",
  "skin",
  "sr.1.0.js",
  "static",
  "web_s",
]);

function parseUrl(value, label) {
  try {
    return new URL(value);
  } catch {
    throw new Error(`${label} 형식이 올바르지 않습니다.`);
  }
}

function validateCdpUrl(value) {
  const parsed = parseUrl(value, "--cdp-url");
  const hasExtraData =
    parsed.username !== "" || parsed.password !== "" || parsed.search !== "" || parsed.hash !== "";
  if (!["http:", "https:"].includes(parsed.protocol) || !LOOPBACK_HOSTS.has(parsed.hostname)) {
    throw new Error("--cdp-url은 loopback HTTP(S) 주소여야 합니다.");
  }
  if (hasExtraData || (parsed.pathname !== "/" && parsed.pathname !== "")) {
    throw new Error("--cdp-url에는 자격 증명, query, fragment 또는 경로를 넣을 수 없습니다.");
  }
  return parsed.origin;
}

function parseAllowedOrigin(value) {
  const parsed = parseUrl(value, "--allow-origin");
  const hasExtraData =
    parsed.username !== "" || parsed.password !== "" || parsed.search !== "" || parsed.hash !== "";
  if (!["http:", "https:"].includes(parsed.protocol) || hasExtraData) {
    throw new Error("--allow-origin은 자격 증명·query·fragment가 없는 HTTP(S) origin이어야 합니다.");
  }
  if (parsed.pathname !== "/" && parsed.pathname !== "") {
    throw new Error("--allow-origin에는 path를 넣을 수 없습니다.");
  }
  return parsed.origin;
}

function parseArgs(argv) {
  const values = {
    cdpUrl: DEFAULT_CDP_URL,
    durationMs: DEFAULT_DURATION_MS,
    maxRecords: DEFAULT_MAX_RECORDS,
    allowOrigins: [],
  };

  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    const next = argv[index + 1];
    if (argument === "--cdp-url" && next) {
      values.cdpUrl = validateCdpUrl(next);
      index += 1;
    } else if (argument === "--duration-ms" && next) {
      values.durationMs = /^\d+$/.test(next) ? Number(next) : Number.NaN;
      index += 1;
    } else if (argument === "--max-records" && next) {
      values.maxRecords = /^\d+$/.test(next) ? Number(next) : Number.NaN;
      index += 1;
    } else if (argument === "--allow-origin" && next) {
      values.allowOrigins.push(parseAllowedOrigin(next));
      index += 1;
    } else {
      throw new Error("알 수 없거나 값이 없는 인자가 있습니다.");
    }
  }

  values.cdpUrl = validateCdpUrl(values.cdpUrl);
  if (!Number.isInteger(values.durationMs) || values.durationMs < 1 || values.durationMs > 60_000) {
    throw new Error("--duration-ms는 1~60000 정수여야 합니다.");
  }
  if (!Number.isInteger(values.maxRecords) || values.maxRecords < 1 || values.maxRecords > 2_000) {
    throw new Error("--max-records는 1~2000 정수여야 합니다.");
  }
  if (values.allowOrigins.length === 0) {
    throw new Error("--allow-origin을 하나 이상 지정해야 합니다.");
  }
  return values;
}

function findGlobalNodeModules() {
  const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
  try {
    return execFileSync(npmCommand, ["root", "-g"], {
      encoding: "utf8",
      shell: process.platform === "win32",
      windowsHide: true,
    }).trim();
  } catch {
    return null;
  }
}

async function importPlaywrightCore() {
  try {
    return await import("playwright-core");
  } catch {
    const roots = [
      findGlobalNodeModules(),
      process.env.npm_config_prefix ? path.join(process.env.npm_config_prefix, "node_modules") : null,
      process.env.APPDATA ? path.join(process.env.APPDATA, "npm", "node_modules") : null,
    ].filter(Boolean);

    for (const root of new Set(roots)) {
      const entry = path.join(root, "agbrowse", "node_modules", "playwright-core", "index.mjs");
      try {
        return await import(pathToFileURL(entry).href);
      } catch {
        // 다음 설치 위치를 확인합니다. 후보 경로와 원문 오류는 출력하지 않습니다.
      }
    }
    throw new Error("playwright-core를 찾지 못했습니다.");
  }
}

function normalizeMethod(value) {
  const method = String(value || "").toUpperCase();
  return /^[A-Z]{1,16}$/.test(method) ? method : "OTHER";
}

function normalizeResourceType(value) {
  const type = String(value || "").toLowerCase();
  const allowed = new Set([
    "document",
    "script",
    "stylesheet",
    "image",
    "font",
    "media",
    "xhr",
    "fetch",
  ]);
  return allowed.has(type) ? type : "other";
}

function normalizeInitiator(value) {
  const type = String(value || "").toLowerCase();
  const allowed = new Set(["parser", "script", "preload", "preflight"]);
  return allowed.has(type) ? type : "other";
}

function sanitizeSegment(rawSegment) {
  let segment;
  try {
    segment = decodeURIComponent(rawSegment);
  } catch {
    return "{segment}";
  }
  return SAFE_PATH_SEGMENTS.has(segment) ? segment : "{segment}";
}

function sanitizePathname(pathname) {
  if (pathname.startsWith("/dyna/")) return "/dyna/{dynamic-script}.js";
  if (pathname.startsWith("/web_s/")) return "/web_s/{dynamic}/{dynamic}";
  if (pathname.startsWith("/js/dynapath/")) return "/js/dynapath/{dynamic-script}.js";
  if (pathname.startsWith("/dynaPath/")) return "/dynaPath/{id}/{id}";

  const segments = pathname.split("/").map((segment, index) => {
    if (index === 0 || segment === "") return segment;
    return sanitizeSegment(segment);
  });
  return segments.join("/") || "/";
}

function sanitizeUrl(rawUrl, allowedOrigins) {
  let parsed;
  try {
    parsed = new URL(rawUrl);
  } catch {
    return null;
  }
  // 이 도구는 request/response status를 결합할 수 있는 HTTP(S)만 다룹니다. WebSocket은
  // 별도 CDP 이벤트 계약이 필요하므로 지원하는 것처럼 조용히 누락하지 않고 범위에서 제외합니다.
  if (!["http:", "https:"].includes(parsed.protocol)) return null;
  if (!allowedOrigins.has(parsed.origin)) return null;
  return { origin: parsed.origin, path: sanitizePathname(parsed.pathname) };
}

function safeStatus(value) {
  const status = Number(value);
  return Number.isInteger(status) && status >= 100 && status <= 599 ? status : null;
}

function projectRecord(record) {
  return {
    origin: record.origin,
    path: record.path,
    method: record.method,
    status: record.status,
    type: record.type,
    initiator: record.initiator,
  };
}

function summarize(records) {
  const counts = new Map();
  for (const record of records) {
    const projected = projectRecord(record);
    const key = JSON.stringify(projected);
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  return Array.from(counts, ([key, count]) => ({ ...JSON.parse(key), count })).sort((a, b) =>
    `${a.origin}${a.path}${a.method}`.localeCompare(`${b.origin}${b.path}${b.method}`),
  );
}

async function captureNetwork(args, chromium) {
  const allowedOrigins = new Set(args.allowOrigins);
  const browser = await chromium.connectOverCDP(args.cdpUrl);
  const contexts = browser.contexts();
  const pages = contexts.flatMap((context) => context.pages());
  const page = pages.at(-1);
  if (!page) throw new Error("활성 Chrome 페이지가 없습니다.");

  const session = await page.context().newCDPSession(page);
  const pending = new Map();
  const completed = [];
  let capped = false;
  let networkEnabled = false;

  const finalize = (requestId, status = null) => {
    const record = pending.get(requestId);
    if (!record) return;
    pending.delete(requestId);
    if (completed.length >= args.maxRecords) {
      capped = true;
      return;
    }
    completed.push({ ...record, status: status ?? record.status ?? null });
  };

  const onRequest = (event) => {
    if (event.redirectResponse) finalize(event.requestId, safeStatus(event.redirectResponse.status));
    if (completed.length + pending.size >= args.maxRecords) {
      capped = true;
      return;
    }

    const safeUrl = sanitizeUrl(event.request.url, allowedOrigins);
    if (!safeUrl) return;
    pending.set(event.requestId, {
      ...safeUrl,
      method: normalizeMethod(event.request.method),
      status: null,
      type: normalizeResourceType(event.type),
      initiator: normalizeInitiator(event.initiator?.type),
    });
  };
  const onResponse = (event) => {
    const record = pending.get(event.requestId);
    if (record) record.status = safeStatus(event.response.status);
  };
  const onFinished = (event) => finalize(event.requestId);
  const onFailed = (event) => finalize(event.requestId);

  session.on("Network.requestWillBeSent", onRequest);
  session.on("Network.responseReceived", onResponse);
  session.on("Network.loadingFinished", onFinished);
  session.on("Network.loadingFailed", onFailed);

  try {
    await session.send("Network.enable");
    networkEnabled = true;
    process.stderr.write("READY\n");
    await new Promise((resolve) => setTimeout(resolve, args.durationMs));
  } finally {
    // 먼저 새 이벤트 유입을 막아 종료 직전 request가 pending에 남는 race를 제거합니다.
    session.off("Network.requestWillBeSent", onRequest);
    session.off("Network.responseReceived", onResponse);
    session.off("Network.loadingFinished", onFinished);
    session.off("Network.loadingFailed", onFailed);
    if (networkEnabled) {
      try {
        await session.send("Network.disable");
      } catch {
        // detach를 계속 시도하고 원문 CDP 오류는 출력하지 않습니다.
      }
    }
    for (const requestId of Array.from(pending.keys())) finalize(requestId);
    try {
      await session.detach();
    } catch {
      // process 종료로 transport만 정리하며 Browser.close()는 호출하지 않습니다.
    }
  }

  return {
    schema: "browser-network-summary-v2",
    redaction: "fail-closed-path-allowlist-v1",
    capped,
    records: summarize(completed),
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const { chromium } = await importPlaywrightCore();
  const output = `${JSON.stringify(await captureNetwork(args, chromium), null, 2)}\n`;
  await new Promise((resolve) => process.stdout.write(output, resolve));

  // connectOverCDP의 Browser.close()는 사용자의 Chrome 자체를 종료합니다.
  // 명시적으로 process를 끝내 transport만 끊고 브라우저는 그대로 둡니다.
  process.exit(0);
}

const isEntrypoint = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
if (isEntrypoint) {
  main().catch((error) => {
    const errorName = error instanceof Error && error.name ? error.name : "Error";
    process.stderr.write(`캡처 실패 (${errorName}). 원문 오류는 출력하지 않았습니다.\n`);
    process.exit(1);
  });
}

export {
  captureNetwork,
  parseArgs,
  sanitizePathname,
  sanitizeSegment,
  sanitizeUrl,
  summarize,
  validateCdpUrl,
};
