const KORAIL_OFFICIAL_ENTRY_URL = "https://www.korail.com/ticket/search/general";
const KORAIL_OFFICIAL_SEARCH_ORIGIN = "https://www.korail.com";
const KORAIL_OFFICIAL_SEARCH_PATH = "/ticket/search/list";

const REQUIRED_KEYS = new Set([
  "srtCheckYn",
  "ebizCrossCheck",
  "adjStnScdlOfrFlg",
  "adjStnScdlOfrFlg2",
  "rtYn",
  "txtMenuId",
  "radJobId",
  "searchType",
  "txtGoStart",
  "txtGoEnd",
  "txtGoStartCode",
  "txtGoEndCode",
  "txtGoAbrdDt",
  "txtGoHour",
  "txtPsgFlg_1",
  "txtPsgFlg_2",
  "txtPsgFlg_3",
  "txtPsgFlg_4",
  "txtPsgFlg_5",
  "txtPsgFlg_8",
  "selGoSeat1",
  "txtSeatAttCd_4",
  "txtTrnGpCd",
  "tkTripChgQryFlg",
  "txtWkndUseFlg",
]);

const FIXED_VALUES: Readonly<Record<string, string>> = {
  srtCheckYn: "N",
  ebizCrossCheck: "N",
  adjStnScdlOfrFlg: "N",
  adjStnScdlOfrFlg2: "N",
  rtYn: "N",
  txtMenuId: "11",
  radJobId: "1",
  searchType: "GENERAL",
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
};

function isGregorianDate(value: string): boolean {
  if (!/^\d{8}$/.test(value)) return false;
  const year = Number(value.slice(0, 4));
  const month = Number(value.slice(4, 6));
  const day = Number(value.slice(6, 8));
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return parsed.getUTCFullYear() === year
    && parsed.getUTCMonth() === month - 1
    && parsed.getUTCDate() === day;
}

export function isStrictKorailOfficialSearchUrl(candidate: unknown): candidate is string {
  if (typeof candidate !== "string" || !candidate) return false;
  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    return false;
  }
  if (
    parsed.protocol !== "https:"
    || parsed.origin !== KORAIL_OFFICIAL_SEARCH_ORIGIN
    || parsed.pathname !== KORAIL_OFFICIAL_SEARCH_PATH
    || parsed.username
    || parsed.password
    || parsed.port
    || parsed.hash
  ) {
    return false;
  }

  const entries = [...parsed.searchParams.entries()];
  if (entries.length !== REQUIRED_KEYS.size) return false;
  const keys = new Set(entries.map(([key]) => key));
  if (keys.size !== REQUIRED_KEYS.size || [...keys].some((key) => !REQUIRED_KEYS.has(key))) {
    return false;
  }
  if ([...REQUIRED_KEYS].some((key) => parsed.searchParams.getAll(key).length !== 1)) {
    return false;
  }
  if (Object.entries(FIXED_VALUES).some(([key, value]) => parsed.searchParams.get(key) !== value)) {
    return false;
  }

  const originName = parsed.searchParams.get("txtGoStart")?.trim() ?? "";
  const destinationName = parsed.searchParams.get("txtGoEnd")?.trim() ?? "";
  const originCode = parsed.searchParams.get("txtGoStartCode") ?? "";
  const destinationCode = parsed.searchParams.get("txtGoEndCode") ?? "";
  const travelDate = parsed.searchParams.get("txtGoAbrdDt") ?? "";
  const departureTime = parsed.searchParams.get("txtGoHour") ?? "";
  return Boolean(originName)
    && Boolean(destinationName)
    && originName !== destinationName
    && /^\d{4}$/.test(originCode)
    && /^\d{4}$/.test(destinationCode)
    && originCode !== destinationCode
    && isGregorianDate(travelDate)
    && /^(?:[01]\d|2[0-3])0000$/.test(departureTime);
}

export function korailOfficialSearchUrlOrEntry(candidate: unknown): string {
  return isStrictKorailOfficialSearchUrl(candidate) ? candidate : KORAIL_OFFICIAL_ENTRY_URL;
}

export { KORAIL_OFFICIAL_ENTRY_URL };
