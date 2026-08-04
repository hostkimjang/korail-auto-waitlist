#!/usr/bin/env node

import fs from "node:fs";
import process from "node:process";
import { pathToFileURL } from "node:url";

const SAFE_FORM_FIELDS = new Set([
  "Device",
  "Version",
  "adjStnScdlOfrFlg",
  "adjStnScdlOfrFlg2",
  "ebizCrossCheck",
  "limitStartDate",
  "radJobId",
  "rtYn",
  "searchType",
  "spePersonnel",
  "srtCheckYn",
  "tkTripChgQryFlg",
  "txtGoAbrdDt",
  "txtGoEnd",
  "txtGoEndCode",
  "txtGoHour",
  "txtGoStart",
  "txtGoStartCode",
  "txtMenuId",
  "txtPsgFlg_1",
  "txtSeatAttCd_4",
  "txtTrainNm",
  "txtTrnGpCd",
  "txtWkndUseFlg",
]);

const SAFE_HEADER_NAMES = new Set([
  "accept",
  "accept-language",
  "access-control-allow-methods",
  "access-control-allow-origin",
  "cache-control",
  "content-type",
  "origin",
  "pragma",
  "referer",
  "sec-ch-ua",
  "sec-ch-ua-mobile",
  "sec-ch-ua-platform",
  "sec-fetch-dest",
  "sec-fetch-mode",
  "sec-fetch-site",
  "user-agent",
]);

const SENSITIVE_HEADER_NAMES = new Set([
  "authorization",
  "cookie",
  "proxy-authorization",
  "set-cookie",
  "x-rail-bridge-challenge",
  "x-rail-bridge-client-id",
  "x-rail-bridge-token",
]);

function readTextAuto(path) {
  const buffer = fs.readFileSync(path);
  if (buffer.length >= 2 && buffer[0] === 0xff && buffer[1] === 0xfe) {
    return buffer.subarray(2).toString("utf16le");
  }
  const utf8 = buffer.toString("utf8").replace(/^\ufeff/, "");
  if (utf8.includes("\u0000")) {
    return buffer.toString("utf16le").replace(/^\ufeff/, "");
  }
  return utf8;
}

function parseArgs(argv) {
  const values = { manual: null, auto: null, output: null };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    const next = argv[index + 1];
    if (argument === "--manual" && next) {
      values.manual = next;
      index += 1;
    } else if (argument === "--auto" && next) {
      values.auto = next;
      index += 1;
    } else if (argument === "--output" && next) {
      values.output = next;
      index += 1;
    } else {
      throw new Error("usage: compare-korail-captures --manual <ndjson> --auto <json>");
    }
  }
  if (!values.manual || !values.auto) {
    throw new Error("both --manual and --auto are required");
  }
  return values;
}

function safeStatus(value) {
  const status = Number(value);
  return Number.isInteger(status) && status >= 100 && status <= 599 ? status : null;
}

function safeMethod(value) {
  const method = String(value || "").toUpperCase();
  return /^[A-Z]{1,16}$/.test(method) ? method : null;
}

function safeHeaderName(value) {
  const name = String(value || "").trim().toLowerCase();
  if (SENSITIVE_HEADER_NAMES.has(name)) return "{sensitive-header}";
  if (SAFE_HEADER_NAMES.has(name)) return name;
  return "{header}";
}

function safeFormFieldName(value) {
  const name = String(value || "").trim();
  if (SAFE_FORM_FIELDS.has(name)) return name;
  return "{field}";
}

function uniqueSorted(values) {
  return Array.from(new Set(values)).sort();
}

function parseMultipartFieldNames(text) {
  if (!text) return [];
  const fields = [];
  const pattern = /name="([^"]+)"\r\n\r\n[\s\S]*?\r\n(?=--)/g;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    fields.push(safeFormFieldName(match[1]));
  }
  return uniqueSorted(fields);
}

function safeHeaderKeys(headers) {
  if (!headers || typeof headers !== "object") return [];
  return uniqueSorted(Object.keys(headers).map(safeHeaderName));
}

function classifyResponse({ body = "", error = "", detail = {} } = {}) {
  const text = `${body || ""}\n${error || ""}\n${JSON.stringify(detail || {})}`;
  if (/IRG000000/.test(text)) return "success";
  if (/macro_err1|-8001|-8002|-8003|captcha|netfunnel/i.test(text)) return "restricted";
  if (!text.trim()) return "missing";
  return "other";
}

function loadManualCapture(path) {
  return readTextAuto(path)
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

function protectedRequestFromManual(rows) {
  return rows.find((row) => row.kind === "request" && String(row.url || "").includes("/web_s/"))
    || {};
}

function protectedResponseFromManual(rows) {
  return rows.find((row) => row.kind === "response" && String(row.url || "").includes("/web_s/"))
    || {};
}

function browserSummary(browser) {
  if (!browser || typeof browser !== "object") {
    return { headless: null, profile_kind: "unknown" };
  }
  const rawProfile = String(browser.user_data_dir || "");
  let profileKind = "persistent_or_unknown";
  if (/playwright/i.test(rawProfile)) {
    profileKind = "temporary_playwright";
  } else if (/\.browser-agent|browser-profile/i.test(rawProfile)) {
    profileKind = "automation_profile";
  }
  return {
    headless: typeof browser.headless === "boolean" ? browser.headless : null,
    profile_kind: profileKind,
  };
}

function summarizeManual(path) {
  const rows = loadManualCapture(path);
  const request = protectedRequestFromManual(rows);
  const response = protectedResponseFromManual(rows);
  return {
    request_seen: Boolean(request.url),
    response_seen: Boolean(response.url),
    method: safeMethod(request.method),
    status: safeStatus(response.status),
    app_result: classifyResponse({ body: response.body }),
    header_keys: safeHeaderKeys(request.headers),
    field_keys: parseMultipartFieldNames(request.postData),
  };
}

function summarizeAutomation(path) {
  const data = JSON.parse(readTextAuto(path));
  const bridge = data.bridge && typeof data.bridge === "object" ? data.bridge : {};
  const request = bridge.sanitizedProtectedRequest
    || bridge.protected_request
    || bridge.protectedRequest
    || {};
  const response = bridge.protected_response || bridge.protectedResponse || {};
  const fieldKeys = parseMultipartFieldNames(request.postData);
  const headerKeys = safeHeaderKeys(request.headers);
  let requestCaptureLevel = "missing";
  if (request.url || request.method) requestCaptureLevel = "metadata_only";
  if (fieldKeys.length > 0 || headerKeys.length > 0) requestCaptureLevel = "body_or_headers";

  return {
    request_seen: Boolean(request.url || bridge.protected_url),
    response_seen: response.status !== undefined,
    request_capture_level: requestCaptureLevel,
    method: safeMethod(request.method),
    status: safeStatus(response.status),
    app_result: classifyResponse({ body: response.body, error: data.error, detail: data.detail }),
    error_code: typeof data.detail?.err_code === "string" ? data.detail.err_code : data.error || null,
    dyna_result_code: typeof data.detail?.dyna_result_code === "string"
      ? data.detail.dyna_result_code
      : null,
    browser: browserSummary(bridge.browser),
    header_keys: headerKeys,
    field_keys: fieldKeys,
  };
}

function difference(left, right) {
  const leftSet = new Set(left);
  const rightSet = new Set(right);
  return {
    manual_only: left.filter((item) => !rightSet.has(item)),
    automation_only: right.filter((item) => !leftSet.has(item)),
    common: left.filter((item) => rightSet.has(item)),
  };
}

function buildComparison({ manualPath, autoPath }) {
  const manual = summarizeManual(manualPath);
  const automation = summarizeAutomation(autoPath);
  return {
    schema: "korail-manual-automation-capture-comparison-v1",
    redaction: {
      kept: [
        "HTTP method",
        "HTTP status",
        "application result class",
        "allowlisted header names",
        "allowlisted form field names",
        "browser context class",
      ],
      discarded: [
        "raw URL and dynamic path",
        "query string",
        "request and response bodies",
        "header values",
        "cookies",
        "authorization values",
        "request identifiers",
        "raw profile paths",
      ],
    },
    manual,
    automation,
    differences: {
      same_http_status: manual.status !== null && manual.status === automation.status,
      same_application_result: manual.app_result === automation.app_result,
      headers: difference(manual.header_keys, automation.header_keys),
      fields: difference(manual.field_keys, automation.field_keys),
    },
    assessment: {
      http_status_is_sufficient: false,
      raw_request_equivalence_proven: false,
      likely_failure_layer: automation.app_result === "restricted"
        ? "application_protection_or_session_validation"
        : "unclassified",
    },
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const report = buildComparison({ manualPath: args.manual, autoPath: args.auto });
  const output = `${JSON.stringify(report, null, 2)}\n`;
  if (args.output) {
    fs.writeFileSync(args.output, output, "utf8");
  } else {
    process.stdout.write(output);
  }
}

const isEntrypoint = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
if (isEntrypoint) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : "comparison failed"}\n`);
    process.exit(1);
  });
}

export {
  buildComparison,
  parseArgs,
  parseMultipartFieldNames,
  readTextAuto,
  safeFormFieldName,
  safeHeaderName,
};
