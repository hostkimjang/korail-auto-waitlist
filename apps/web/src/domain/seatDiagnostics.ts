export const seatObservationReasons = [
  "source_not_configured",
  "provider_access_restricted",
  "unsupported_route",
  "passenger_count_not_supported",
  "departure_window_elapsed",
  "no_exact_match",
  "source_unavailable",
  "public_api_not_available",
  "invalid_provider_payload",
  "invalid_provider_provenance",
] as const;

export type SeatObservationReason = (typeof seatObservationReasons)[number];

export interface SeatObservationReasonMeta {
  label: string;
  helper: string;
}

const reasonSet = new Set<string>(seatObservationReasons);

const reasonMeta: Record<SeatObservationReason, SeatObservationReasonMeta> = {
  source_not_configured: {
    label: "서버 좌석 조회 미설정",
    helper: "서버의 KORAIL Chromium adapter 설정을 확인한 뒤 다시 조회해 주세요.",
  },
  provider_access_restricted: {
    label: "조회 제한",
    helper: "운영사가 현재 좌석 조회를 제한해 상태를 가져오지 못했습니다.",
  },
  unsupported_route: {
    label: "구간 미지원",
    helper: "현재 좌석 조회 방식이 이 출발역·도착역 조합을 지원하지 않습니다.",
  },
  passenger_count_not_supported: {
    label: "인원 미지원",
    helper: "선택한 인원 수의 좌석 상태를 조회할 수 없습니다.",
  },
  departure_window_elapsed: {
    label: "출발 시간 경과",
    helper: "이미 운행이 끝난 시간대라 현재 좌석 상태를 다시 조회하지 않습니다.",
  },
  no_exact_match: {
    label: "일치 정보 없음",
    helper: "열차번호·날짜·출발시각이 일치하는 좌석 응답이 없습니다.",
  },
  source_unavailable: {
    label: "조회 지연",
    helper: "좌석 정보 제공원이 응답하지 않아 상태를 가져오지 못했습니다.",
  },
  public_api_not_available: {
    label: "정보 미제공",
    helper: "공개 시간표에는 좌석 재고가 포함되지 않습니다.",
  },
  invalid_provider_payload: {
    label: "응답 확인 불가",
    helper: "좌석 응답 형식을 확인할 수 없어 상태를 표시하지 않습니다.",
  },
  invalid_provider_provenance: {
    label: "근거 확인 불가",
    helper: "관측 근거가 유효하지 않아 좌석 상태를 표시하지 않습니다.",
  },
};

export function normalizeSeatObservationReason(value: unknown): SeatObservationReason {
  return typeof value === "string" && reasonSet.has(value)
    ? value as SeatObservationReason
    : "public_api_not_available";
}

export function seatObservationReasonMeta(value: unknown): SeatObservationReasonMeta {
  return reasonMeta[normalizeSeatObservationReason(value)];
}
