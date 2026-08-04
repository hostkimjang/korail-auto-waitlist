import { JSDOM } from "jsdom";
import { describe, expect, it } from "vitest";

import { readCurrentKorailResults } from "../src/dom-reader";

function requiredElement(document: Document, selector: string): Element {
  const element = document.querySelector(selector);
  if (element === null) {
    throw new Error(`Missing fixture element: ${selector}`);
  }
  return element;
}

function renderedDocument(extraText = ""): Document {
  const dom = new JSDOM(`<!doctype html><body>
    <input id="startDate" value="2026-07-31(금) 12:00" />
    <p>어른 1명</p>
    <p>${extraText}</p>
    <ul>
      <li class="tckList">
        <div class="tck_inner">
          <div class="tit_box"><span class="num">0026</span></div>
          <div class="data_box right"><h3>대전 → 서울(12:00 ~ 13:04)</h3></div>
          <div class="price_box gen sold_out"><span>매진</span></div>
          <div class="price_box spe sold_out_soon"><span>특실(매진임박)</span></div>
        </div>
      </li>
    </ul>
  </body>`, { url: "https://www.korail.com/ticket/search/list" });
  Object.defineProperty(dom.window.HTMLElement.prototype, "getClientRects", {
    configurable: true,
    value: () => [{ x: 0, y: 0, width: 1, height: 1 }],
  });
  Object.defineProperty(dom.window.document.body, "innerText", {
    configurable: true,
    get() {
      return this.textContent ?? "";
    },
  });
  return dom.window.document;
}

describe("readCurrentKorailResults", () => {
  it("reads only a complete visible KORAIL result card", () => {
    expect(readCurrentKorailResults(renderedDocument())).toEqual({
      ok: true,
      payload: {
        origin: "대전",
        destination: "서울",
        travel_date: "2026-07-31",
        passenger_count: 1,
        trains: [{
          train_number: "0026",
          departure_at: "2026-07-31T12:00:00+09:00",
          standard: "sold_out",
          first: "limited",
        }],
      },
    });
  });

  it("fails closed when an access-protection marker is visible", () => {
    expect(readCurrentKorailResults(renderedDocument("CODE -8003 macro_err1")))
      .toEqual({ ok: false, code: "blocked" });
  });

  it("reads standing-plus-seat and a bare unavailable-class marker by gen/spe identity", () => {
    const document = renderedDocument();
    const standard = requiredElement(document, ".price_box.gen");
    const first = requiredElement(document, ".price_box.spe");
    standard.classList.remove("sold_out");
    standard.textContent = "일반실(입석+예매) 48,800원";
    first.classList.remove("sold_out_soon");
    first.textContent = "-";

    const result = readCurrentKorailResults(document);
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.payload.trains[0]).toMatchObject({
        standard: "standing_plus_seat",
        first: "not_offered",
      });
    }
  });

  it("uses gen/spe identity instead of DOM order", () => {
    const document = renderedDocument();
    const inner = requiredElement(document, ".tck_inner");
    const standard = requiredElement(document, ".price_box.gen");
    const first = requiredElement(document, ".price_box.spe");
    inner.insertBefore(first, standard);

    const result = readCurrentKorailResults(document);
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.payload.trains[0]).toMatchObject({ standard: "sold_out", first: "limited" });
    }
  });

  it("falls back to exact two-box order only when both unlabeled boxes are sold out", () => {
    const document = renderedDocument();
    for (const box of document.querySelectorAll(".price_box")) {
      box.classList.remove("gen", "spe", "sold_out_soon");
      box.classList.add("sold_out");
      box.textContent = "매진";
    }

    const result = readCurrentKorailResults(document);
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.payload.trains[0]).toMatchObject({
        standard: "sold_out",
        first: "sold_out",
      });
    }
  });

  it("fails closed when a seat box has no recognizable status", () => {
    const document = renderedDocument();
    const standard = requiredElement(document, ".price_box.gen");
    standard.classList.remove("sold_out");
    standard.textContent = "상태를 확인하세요";
    expect(readCurrentKorailResults(document)).toEqual({ ok: false, code: "parse_failed" });
  });
});
