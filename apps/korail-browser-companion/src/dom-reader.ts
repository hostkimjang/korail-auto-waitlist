import { parseKorailSnapshot } from "./parser";
import type { KorailRenderedResultInput, KorailSnapshotPayload } from "./types";

const PROTECTION_MARKER = /(?:code\s*-?\s*800[23]|macro_err1|captcha|netfunnel|비정상\s*접근)/i;
const ROUTE_HEADING = /^(.+?)\s*→\s*(.+?)\s*\(\s*(\d{2}:\d{2})\s*~\s*\d{2}:\d{2}\s*\)$/;

function visibleText(element: Element): string {
  return element.textContent?.replace(/\s+/g, " ").trim() ?? "";
}

function isVisible(element: Element, document: Document): boolean {
  if (element.hasAttribute("hidden") || element.getAttribute("aria-hidden") === "true") {
    return false;
  }
  const style = document.defaultView?.getComputedStyle(element);
  if (style?.display === "none" || style?.visibility === "hidden") {
    return false;
  }
  return element.getClientRects().length > 0;
}

function visibleElements(root: ParentNode, selector: string, document: Document): Element[] {
  return Array.from(root.querySelectorAll(selector)).filter((element) => isVisible(element, document));
}

function seatBoxClassNames(element: Element, document: Document): string[] {
  return [
    ...Array.from(element.classList),
    ...visibleElements(element, ".sold_out, .sold_out_soon", document).flatMap((item) =>
      Array.from(item.classList),
    ),
  ];
}

function isSoldOutBox(element: Element, document: Document): boolean {
  const text = visibleText(element);
  const classes = seatBoxClassNames(element, document);
  return classes.includes("sold_out") || /^\s*매진\s*$/.test(text);
}

function identifySeatBoxes(
  priceBoxes: readonly Element[],
  document: Document,
): { standard: Element; first: Element } | null {
  if (priceBoxes.length !== 2) {
    return null;
  }

  const standardByClass = priceBoxes.find((box) => box.matches(".price_box.gen"));
  const firstByClass = priceBoxes.find((box) => box.matches(".price_box.spe"));
  if (standardByClass !== undefined && firstByClass !== undefined) {
    return { standard: standardByClass, first: firstByClass };
  }

  const standardByText = priceBoxes.find((box) => /일반실/.test(visibleText(box)));
  const firstByText = priceBoxes.find((box) => /특실/.test(visibleText(box)));
  if (standardByText !== undefined && firstByText !== undefined) {
    return { standard: standardByText, first: firstByText };
  }

  if (standardByClass !== undefined || firstByClass !== undefined) {
    const identified = standardByClass ?? firstByClass;
    const remaining = priceBoxes.find((box) => box !== identified);
    if (remaining === undefined) {
      return null;
    }
    if (standardByClass !== undefined) {
      return { standard: standardByClass, first: remaining };
    }
    if (firstByClass !== undefined) {
      return { standard: remaining, first: firstByClass };
    }
  }

  if (priceBoxes.every((box) => isSoldOutBox(box, document))) {
    const standard = priceBoxes[0];
    const first = priceBoxes[1];
    return standard !== undefined && first !== undefined ? { standard, first } : null;
  }
  return null;
}

function extractTravelDate(document: Document, visiblePageText: string): string | null {
  const dateInput = document.querySelector<HTMLInputElement>("#startDate");
  const inputMatch = dateInput?.value.match(/\b(20\d{2})-(\d{2})-(\d{2})\b/);
  if (dateInput !== null && isVisible(dateInput, document) && inputMatch != null) {
    return `${inputMatch[1]}-${inputMatch[2]}-${inputMatch[3]}`;
  }
  const matches = [
    ...visiblePageText.matchAll(/\b(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})\b/g),
    ...visiblePageText.matchAll(/(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일/g),
  ];
  const dates = new Set<string>();
  for (const match of matches) {
    const year = Number(match[1]);
    const month = Number(match[2]);
    const day = Number(match[3]);
    if (month < 1 || month > 12 || day < 1 || day > 31) {
      return null;
    }
    dates.add(`${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`);
  }
  if (dates.size !== 1) {
    return null;
  }
  return Array.from(dates)[0] ?? null;
}

function hasConfirmedSinglePassenger(visiblePageText: string): boolean {
  const singular = /(?:어른|성인|승객(?:\s*수)?)\s*[:：]?\s*1\s*명?/.test(visiblePageText);
  const conflicting = /(?:어른|성인|승객(?:\s*수)?)\s*[:：]?\s*(?:0|[2-9]\d*)\s*명?/.test(
    visiblePageText,
  );
  return singular && !conflicting;
}

function readRow(element: Element, document: Document): KorailRenderedResultInput | null {
  const inner = visibleElements(element, ".tck_inner", document)[0];
  const trainNumber = inner === undefined ? undefined : visibleElements(inner, ".tit_box .num", document)[0];
  const routeBox = inner === undefined ? undefined : visibleElements(inner, ".data_box.right", document)[0];
  const priceBoxes = inner === undefined ? [] : visibleElements(inner, ".price_box", document);
  if (
    trainNumber === null ||
    trainNumber === undefined ||
    routeBox === null ||
    routeBox === undefined ||
    priceBoxes.length !== 2
  ) {
    return null;
  }
  const routeCandidate = [
    routeBox,
    ...visibleElements(routeBox, "h1, h2, h3, h4, h5, h6, strong", document),
  ].find((candidate) => ROUTE_HEADING.test(visibleText(candidate)));
  const route = routeCandidate === undefined ? null : ROUTE_HEADING.exec(visibleText(routeCandidate));
  const seatBoxes = identifySeatBoxes(priceBoxes, document);
  if (route === null || seatBoxes === null) {
    return null;
  }
  const { standard: standardBox, first: firstBox } = seatBoxes;
  return {
    trainNumber: visibleText(trainNumber),
    origin: route[1] ?? "",
    destination: route[2] ?? "",
    departureTime: route[3] ?? "",
    standardText: visibleText(standardBox),
    firstText: visibleText(firstBox),
    standardClassNames: seatBoxClassNames(standardBox, document),
    firstClassNames: seatBoxClassNames(firstBox, document),
  };
}

export type DocumentReadResult =
  | { ok: true; payload: KorailSnapshotPayload }
  | { ok: false; code: "blocked" | "passenger_unverified" | "parse_failed" };

export function readCurrentKorailResults(document: Document): DocumentReadResult {
  const body = document.body;
  if (body === null || !isVisible(body, document)) {
    return { ok: false, code: "parse_failed" };
  }
  const pageText = body.innerText;
  if (PROTECTION_MARKER.test(pageText)) {
    return { ok: false, code: "blocked" };
  }
  if (!hasConfirmedSinglePassenger(pageText)) {
    return { ok: false, code: "passenger_unverified" };
  }
  const travelDate = extractTravelDate(document, pageText);
  const rows = visibleElements(document, "li.tckList", document).map((row) => readRow(row, document));
  if (travelDate === null || rows.some((row) => row === null)) {
    return { ok: false, code: "parse_failed" };
  }
  const payload = parseKorailSnapshot({
    travelDate,
    passengerCount: 1,
    rows: rows.filter((row): row is KorailRenderedResultInput => row !== null),
  });
  return payload === null ? { ok: false, code: "parse_failed" } : { ok: true, payload };
}
