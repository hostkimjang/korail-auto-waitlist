import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";

import {
  captureNetwork,
  parseArgs,
  sanitizePathname,
  sanitizeSegment,
  sanitizeUrl,
  summarize,
  validateCdpUrl,
} from "./capture-browser-network.mjs";

class FakeSession extends EventEmitter {
  constructor({ failEnable = false, failDisable = false, onEnable = null } = {}) {
    super();
    this.failEnable = failEnable;
    this.failDisable = failDisable;
    this.onEnable = onEnable;
    this.detached = false;
    this.disabled = false;
  }

  async send(command) {
    if (command === "Network.enable") {
      if (this.failEnable) throw new Error("synthetic enable failure");
      queueMicrotask(() => {
        if (this.onEnable) {
          this.onEnable(this);
          return;
        }
        for (let index = 0; index < 3; index += 1) {
          this.emit("Network.requestWillBeSent", {
            requestId: `request-${index}`,
            request: { url: `https://example.com/private-${index}`, method: "GET" },
            type: "XHR",
            initiator: { type: "script" },
          });
        }
      });
    }
    if (command === "Network.disable") {
      this.disabled = true;
      // 종료 경계에서 발생한 이벤트가 listener 제거 뒤 무시되는지 확인합니다.
      this.emit("Network.requestWillBeSent", {
        requestId: "late-request",
        request: { url: "https://example.com/late-secret", method: "GET" },
        type: "XHR",
        initiator: { type: "script" },
      });
      if (this.failDisable) throw new Error("synthetic disable failure");
    }
  }

  async detach() {
    this.detached = true;
  }
}

function fakeChromium(session) {
  const page = { context: () => ({ newCDPSession: async () => session }) };
  return {
    connectOverCDP: async () => ({
      contexts: () => [{ pages: () => [page] }],
    }),
  };
}

test("CDP 연결은 자격 증명 없는 loopback HTTP(S)만 허용한다", () => {
  assert.equal(validateCdpUrl("http://127.0.0.1:9222"), "http://127.0.0.1:9222");
  assert.throws(() => validateCdpUrl("https://example.com:9222"));
  assert.throws(() => validateCdpUrl("http://user:secret@127.0.0.1:9222"));
  assert.throws(() => validateCdpUrl("http://127.0.0.1:9222/?token=secret"));
});

test("허용하지 않은 path segment는 길이나 모양과 무관하게 폐기한다", () => {
  assert.equal(sanitizeSegment("user_kimjang"), "{segment}");
  assert.equal(sanitizeSegment("12345"), "{segment}");
  assert.equal(sanitizeSegment("sessionabcdefghijklmnop"), "{segment}");
  assert.equal(sanitizeSegment("accountname.abcdef12.js"), "{segment}");
  assert.equal(sanitizeSegment("macro.do"), "macro.do");
});

test("알려진 동적 경로는 원시 식별자 없이 template으로 만든다", () => {
  assert.equal(sanitizePathname("/dyna/raw-secret.js"), "/dyna/{dynamic-script}.js");
  assert.equal(sanitizePathname("/web_s/user_123/session_456"), "/web_s/{dynamic}/{dynamic}");
  assert.equal(
    sanitizePathname("/js/dynapath/accountname.abcdef12.js"),
    "/js/dynapath/{dynamic-script}.js",
  );
  assert.equal(sanitizePathname("/dynaPath/raw-user/raw-token"), "/dynaPath/{id}/{id}");
});

test("URL 투영은 allowlist origin만 남기고 query·fragment·userinfo를 버린다", () => {
  const origins = new Set(["https://example.com"]);
  assert.deepEqual(
    sanitizeUrl("https://user:secret@example.com/com/macro.do?token=secret#fragment", origins),
    { origin: "https://example.com", path: "/com/macro.do" },
  );
  assert.equal(sanitizeUrl("https://other.example/com/macro.do", origins), null);
  assert.equal(sanitizeUrl("wss://example.com/socket", origins), null);
});

test("요약 record는 최소 필드와 명시된 count만 보존한다", () => {
  const record = {
    origin: "https://example.com",
    path: "/com/macro.do",
    method: "GET",
    status: 200,
    type: "xhr",
    initiator: "script",
  };
  assert.deepEqual(summarize([record, record]), [{ ...record, count: 2 }]);
});

test("명령행은 명시적 allow-origin과 정수 상한을 요구한다", () => {
  assert.deepEqual(parseArgs(["--allow-origin", "https://example.com"]), {
    cdpUrl: "http://127.0.0.1:9222",
    durationMs: 15_000,
    maxRecords: 2_000,
    allowOrigins: ["https://example.com"],
  });
  assert.throws(() => parseArgs([]));
  assert.throws(() => parseArgs(["--allow-origin", "https://example.com/path"]));
  assert.throws(() =>
    parseArgs(["--allow-origin", "https://example.com", "--duration-ms", "10junk"]),
  );
  assert.throws(() =>
    parseArgs(["--allow-origin", "https://example.com", "--max-records", "2x"]),
  );
});

test("동시 pending 요청도 cap에 포함하고 종료 전에 listener를 제거한다", async () => {
  const session = new FakeSession();
  const result = await captureNetwork(
    {
      cdpUrl: "http://127.0.0.1:9222",
      durationMs: 1,
      maxRecords: 2,
      allowOrigins: ["https://example.com"],
    },
    fakeChromium(session),
  );

  assert.equal(result.capped, true);
  assert.equal(result.records.reduce((sum, record) => sum + record.count, 0), 2);
  assert.equal(result.records.every((record) => record.path === "/{segment}"), true);
  assert.equal(session.disabled, true);
  assert.equal(session.detached, true);
});

test("Network.enable 실패도 session을 detach한다", async () => {
  const session = new FakeSession({ failEnable: true });
  await assert.rejects(() =>
    captureNetwork(
      {
        cdpUrl: "http://127.0.0.1:9222",
        durationMs: 1,
        maxRecords: 2,
        allowOrigins: ["https://example.com"],
      },
      fakeChromium(session),
    ),
  );
  assert.equal(session.detached, true);
});

test("redirect hop과 최종 response status를 각각 결합한다", async () => {
  const session = new FakeSession({
    onEnable: (target) => {
      target.emit("Network.requestWillBeSent", {
        requestId: "redirected",
        request: { url: "https://example.com/com/macro.do", method: "GET" },
        type: "XHR",
        initiator: { type: "script" },
      });
      target.emit("Network.requestWillBeSent", {
        requestId: "redirected",
        redirectResponse: { status: 302 },
        request: { url: "https://example.com/dynaPath.do", method: "GET" },
        type: "Script",
        initiator: { type: "parser" },
      });
      target.emit("Network.responseReceived", {
        requestId: "redirected",
        response: { status: 200 },
      });
      target.emit("Network.loadingFinished", { requestId: "redirected" });
    },
  });
  const result = await captureNetwork(
    {
      cdpUrl: "http://127.0.0.1:9222",
      durationMs: 1,
      maxRecords: 3,
      allowOrigins: ["https://example.com"],
    },
    fakeChromium(session),
  );

  assert.deepEqual(
    result.records.map(({ path, status }) => ({ path, status })),
    [
      { path: "/com/macro.do", status: 302 },
      { path: "/dynaPath.do", status: 200 },
    ],
  );
});

test("Network.disable 실패 뒤에도 pending을 정리하고 detach한다", async () => {
  const session = new FakeSession({ failDisable: true });
  const result = await captureNetwork(
    {
      cdpUrl: "http://127.0.0.1:9222",
      durationMs: 1,
      maxRecords: 2,
      allowOrigins: ["https://example.com"],
    },
    fakeChromium(session),
  );

  assert.equal(result.records.reduce((sum, record) => sum + record.count, 0), 2);
  assert.equal(session.detached, true);
});
