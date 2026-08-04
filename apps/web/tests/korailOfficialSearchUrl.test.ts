import { describe, expect, it } from "vitest";

import {
  KORAIL_OFFICIAL_ENTRY_URL,
  isStrictKorailOfficialSearchUrl,
  korailOfficialSearchUrlOrEntry,
} from "../src/features/official-handoff/korailOfficialSearchUrl";

function strictUrl(): string {
  const params = new URLSearchParams({
    srtCheckYn: "N",
    ebizCrossCheck: "N",
    adjStnScdlOfrFlg: "N",
    adjStnScdlOfrFlg2: "N",
    rtYn: "N",
    txtMenuId: "11",
    radJobId: "1",
    searchType: "GENERAL",
    txtGoStart: "대전",
    txtGoEnd: "서울",
    txtGoStartCode: "0010",
    txtGoEndCode: "0001",
    txtGoAbrdDt: "20260803",
    txtGoHour: "050000",
    txtPsgFlg_1: "1",
    txtPsgFlg_2: "0",
    txtPsgFlg_3: "0",
    txtPsgFlg_4: "0",
    txtPsgFlg_5: "0",
    txtPsgFlg_8: "0",
    selGoSeat1: "015",
    txtSeatAttCd_4: "015",
    txtTrnGpCd: "100",
    tkTripChgQryFlg: "Y",
    txtWkndUseFlg: "Y",
  });
  return `https://www.korail.com/ticket/search/list?${params.toString()}`;
}

describe("strict KORAIL official search URL", () => {
  it("accepts the exact Korean route and 25-key general-search contract", () => {
    const candidate = strictUrl();

    expect(isStrictKorailOfficialSearchUrl(candidate)).toBe(true);
    expect(korailOfficialSearchUrlOrEntry(candidate)).toBe(candidate);
  });

  it.each([
    ["extra key", (url: URL) => url.searchParams.set("txtGoTrnNo", "00116")],
    ["duplicate key", (url: URL) => url.searchParams.append("radJobId", "1")],
    ["SRT reservation context", (url: URL) => url.searchParams.set("srtJob", "reserve")],
    ["invalid date", (url: URL) => url.searchParams.set("txtGoAbrdDt", "20260231")],
    ["invalid hour", (url: URL) => url.searchParams.set("txtGoHour", "245500")],
    ["invalid station code", (url: URL) => url.searchParams.set("txtGoStartCode", "10")],
    ["non-default passenger", (url: URL) => url.searchParams.set("txtPsgFlg_1", "2")],
    ["non-KTX train group", (url: URL) => url.searchParams.set("txtTrnGpCd", "109")],
  ])("rejects %s and falls back to the fixed entry", (_label, mutate) => {
    const candidate = new URL(strictUrl());
    mutate(candidate);

    expect(isStrictKorailOfficialSearchUrl(candidate.toString())).toBe(false);
    expect(korailOfficialSearchUrlOrEntry(candidate.toString())).toBe(
      KORAIL_OFFICIAL_ENTRY_URL,
    );
  });

  it.each([
    "https://evil.example/ticket/search/list",
    "https://www.korail.com/ticket/search/general",
    "https://user@www.korail.com/ticket/search/list",
    `${strictUrl()}#fragment`,
  ])("rejects a hostile or non-list URL: %s", (candidate) => {
    expect(isStrictKorailOfficialSearchUrl(candidate)).toBe(false);
  });
});
