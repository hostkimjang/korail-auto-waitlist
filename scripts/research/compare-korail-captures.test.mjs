import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  buildComparison,
  parseArgs,
  parseMultipartFieldNames,
  safeFormFieldName,
  safeHeaderName,
} from "./compare-korail-captures.mjs";

function multipart(fields) {
  const boundary = "----secret-boundary";
  const lines = [];
  for (const [name, value] of Object.entries(fields)) {
    lines.push(`--${boundary}`);
    lines.push(`Content-Disposition: form-data; name="${name}"`);
    lines.push("");
    lines.push(value);
  }
  lines.push(`--${boundary}--`);
  lines.push("");
  return lines.join("\r\n");
}

test("form and header names are allowlisted before reporting", () => {
  assert.equal(safeFormFieldName("txtGoStart"), "txtGoStart");
  assert.equal(safeFormFieldName("sessionToken"), "{field}");
  assert.equal(safeHeaderName("sec-ch-ua"), "sec-ch-ua");
  assert.equal(safeHeaderName("Cookie"), "{sensitive-header}");
  assert.equal(safeHeaderName("X-Unknown-Diagnostic"), "{header}");
});

test("multipart parsing keeps field names only", () => {
  assert.deepEqual(
    parseMultipartFieldNames(multipart({ txtGoStart: "SECRET-STATION", sessionToken: "SECRET" })),
    ["txtGoStart", "{field}"],
  );
});

test("comparison output omits raw URLs, body values, cookies and request ids", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "korail-capture-compare-"));
  const manualPath = path.join(directory, "manual.ndjson");
  const autoPath = path.join(directory, "auto.json");
  const manualRows = [
    {
      kind: "request",
      url: "https://www.korail.com/web_s/SECRET_DYNAMIC?_dg=SECRET_QUERY",
      method: "POST",
      headers: {
        "user-agent": "SECRET-UA",
        cookie: "SECRET-COOKIE",
        "x-private-debug": "SECRET-HEADER",
      },
      postData: multipart({ txtGoStart: "SECRET-STATION", searchType: "1" }),
    },
    {
      kind: "response",
      url: "https://www.korail.com/web_s/SECRET_DYNAMIC?_dg=SECRET_QUERY",
      status: 200,
      body: '{"h_msg_cd":"IRG000000","secret":"SECRET-BODY"}',
    },
  ];
  fs.writeFileSync(manualPath, `${manualRows.map((row) => JSON.stringify(row)).join("\n")}\n`);
  fs.writeFileSync(
    autoPath,
    JSON.stringify({
      bridge: {
        browser: {
          headless: true,
          user_data_dir: "C:/Users/name/.browser-agent-railwait-mobile/browser-profile",
        },
        cookie_header: "SECRET-COOKIE",
        protected_request: {
          method: "POST",
          url: "https://www.korail.com/web_s/SECRET_AUTO?_dg=SECRET_QUERY",
          headers: { authorization: "SECRET-AUTH" },
          postData: multipart({ txtGoStart: "SECRET-STATION", sessionToken: "SECRET" }),
        },
        protected_response: { status: 200, body: '{"err_code":"macro_err1"}' },
      },
      error: "macro_err1",
      detail: { err_code: "macro_err1", dyna_result_code: "-8001", request_id: "SECRET-ID" },
    }),
  );

  const report = buildComparison({ manualPath, autoPath });
  const text = JSON.stringify(report);

  assert.equal(report.manual.app_result, "success");
  assert.equal(report.automation.app_result, "restricted");
  assert.equal(report.differences.same_http_status, true);
  assert.equal(report.differences.same_application_result, false);
  assert.equal(report.automation.browser.profile_kind, "automation_profile");
  assert.ok(report.automation.field_keys.includes("{field}"));
  assert.doesNotMatch(text, /SECRET/);
  assert.doesNotMatch(text, /web_s/);
  assert.doesNotMatch(text, /request_id/);
});

test("command line parser requires both capture files", () => {
  assert.deepEqual(parseArgs(["--manual", "m.ndjson", "--auto", "a.json"]), {
    manual: "m.ndjson",
    auto: "a.json",
    output: null,
  });
  assert.throws(() => parseArgs(["--manual", "m.ndjson"]));
});
