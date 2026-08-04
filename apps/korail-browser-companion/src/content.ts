import { readCurrentKorailResults } from "./dom-reader";
import type { ContentResult } from "./types";

chrome.runtime.onMessage.addListener((message: unknown, _sender, sendResponse) => {
  if (!isCurrentResultsRequest(message)) {
    return;
  }
  const result: ContentResult = readCurrentKorailResults(document);
  sendResponse(result);
});

function isCurrentResultsRequest(message: unknown): boolean {
  return (
    typeof message === "object" &&
    message !== null &&
    "type" in message &&
    message.type === "READ_CURRENT_KORAIL_RESULTS"
  );
}
