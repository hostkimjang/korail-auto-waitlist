import { describe, expect, it } from "vitest";

import { stationCatalogNotice } from "../src/features/new-wait/stationCatalogNotice";

const MEMBERSHIP_NOTICE =
  "이 목록은 운영사별 운행 여부를 증명하지 않으며, 선택 날짜의 시간표 결과에서 실제 운행 열차를 확인합니다.";

describe("station catalog notice", () => {
  it("keeps the existing wording for collected and demo catalogs", () => {
    expect(stationCatalogNotice("official", 279, "")).toBe(
      `공식 역 목록 279개를 불러왔습니다. ${MEMBERSHIP_NOTICE}`,
    );
    expect(stationCatalogNotice("mock", 4, "")).toBe(
      `데모 역 목록 4개를 불러왔습니다. ${MEMBERSHIP_NOTICE}`,
    );
  });

  it("names the bundled snapshot and its Seoul collection date without the internal source", () => {
    const notice = stationCatalogNotice("bundled_snapshot", 279, "2026-11-18T12:35:53.649310+00:00");

    expect(notice).toBe(
      "기본 제공 역 목록 279개를 불러왔습니다. 서버가 공식 역 목록을 새로 받지 못해 "
      + "2026년 11월 18일 기준 목록을 보여 주며, 그 뒤 바뀐 역은 빠져 있을 수 있습니다. "
      + MEMBERSHIP_NOTICE,
    );
    expect(notice).not.toContain("TAGO");
  });

  it("uses the Seoul calendar day across the UTC midnight boundary", () => {
    expect(stationCatalogNotice("bundled_snapshot", 1, "2026-11-18T15:30:00Z"))
      .toContain("2026년 11월 19일 기준");
  });

  it("does not invent a date when the snapshot time is unreadable", () => {
    const notice = stationCatalogNotice("bundled_snapshot", 1, "not-a-date");

    expect(notice).toContain("릴리스에 포함된 목록을 보여 주며");
    expect(notice).not.toMatch(/\d+년/);
  });
});
