import { seoulDateInput } from "./newWaitForm";
import type { StationCatalogState } from "./useStationCatalog";

const MEMBERSHIP_NOTICE =
  "이 목록은 운영사별 운행 여부를 증명하지 않으며, 선택 날짜의 시간표 결과에서 실제 운행 열차를 확인합니다.";

function koreanSeoulDate(value: string): string | null {
  const date = new Date(value);
  if (!value || Number.isNaN(date.getTime())) return null;
  const [year, month, day] = seoulDateInput(date).split("-").map(Number);
  return `${year}년 ${month}월 ${day}일`;
}

export function stationCatalogNotice(
  source: StationCatalogState["source"],
  stationCount: number,
  bundledSnapshotRetrievedAt: string,
): string {
  if (source === "bundled_snapshot") {
    const collectedOn = koreanSeoulDate(bundledSnapshotRetrievedAt);
    const basis = collectedOn ? `${collectedOn} 기준` : "릴리스에 포함된";
    return `기본 제공 역 목록 ${stationCount}개를 불러왔습니다. 서버가 공식 역 목록을 새로 받지 못해 ${basis} 목록을 보여 주며, 그 뒤 바뀐 역은 빠져 있을 수 있습니다. ${MEMBERSHIP_NOTICE}`;
  }
  return `${source === "mock" ? "데모" : "공식"} 역 목록 ${stationCount}개를 불러왔습니다. ${MEMBERSHIP_NOTICE}`;
}
