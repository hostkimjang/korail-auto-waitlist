import type {
  ReservationConfirmationDiagnosticCode,
} from "../../domain/reservationAttempt";

export const reservationConfirmationDiagnosticDescriptions: Readonly<
  Record<ReservationConfirmationDiagnosticCode, string>
> = {
  official_read_unavailable:
    "철도사 공식 내역을 불러오거나 응답을 확인하지 못했습니다.",
  credential_context_mismatch:
    "예매 시도와 공식 확인의 계정 상태가 달라 결과를 연결하지 못했습니다.",
  official_record_ambiguous:
    "공식 내역에서 이번 예매 시도와 정확히 일치하는 항목을 하나로 구분하지 못했습니다.",
  official_evidence_insufficient:
    "공식 내역은 확인했지만 예약 상태를 확정할 정보가 충분하지 않습니다.",
  unspecified:
    "공식 예약 내역 확인으로 결과를 확정하지 못했습니다.",
};
