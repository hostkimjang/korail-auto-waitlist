const PAGE_REQUEST_TYPE = "RAILWAIT_KORAIL_IMPORT_REQUEST";
const PAGE_RESPONSE_TYPE = "RAILWAIT_KORAIL_IMPORT_RESPONSE";

window.addEventListener("message", (event: MessageEvent<unknown>) => {
  if (
    event.source !== window ||
    event.origin !== window.location.origin ||
    !isPageRequest(event.data)
  ) {
    return;
  }

  const requestId = event.data.requestId;
  void chrome.runtime.sendMessage({
    type: "IMPORT_KORAIL_RESULTS_FROM_APP",
    requestId,
  }).then((result: unknown) => {
    window.postMessage({
      type: PAGE_RESPONSE_TYPE,
      requestId,
      result,
    }, window.location.origin);
  }).catch(() => {
    window.postMessage({
      type: PAGE_RESPONSE_TYPE,
      requestId,
      result: { ok: false, code: "extension_unavailable" },
    }, window.location.origin);
  });
});

function isPageRequest(value: unknown): value is { type: string; requestId: string } {
  return (
    typeof value === "object" &&
    value !== null &&
    "type" in value &&
    value.type === PAGE_REQUEST_TYPE &&
    "requestId" in value &&
    typeof value.requestId === "string" &&
    value.requestId.length >= 16 &&
    value.requestId.length <= 128
  );
}
