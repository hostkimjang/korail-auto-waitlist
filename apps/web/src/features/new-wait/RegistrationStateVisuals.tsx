import { CheckCircle, Trash } from "@phosphor-icons/react";

import type { ReservationPolicy } from "../../domain/reservationPolicy";

export interface TrainRegistrationBadgeProps {
  count: number;
}

export function TrainRegistrationBadge({ count }: TrainRegistrationBadgeProps) {
  return (
    <span className="train-registration-badge" aria-label={`대기 등록 ${count}건`}>
      <CheckCircle size={15} weight="fill" aria-hidden="true" />
      대기 등록 {count}건
    </span>
  );
}

export interface SeatRegistrationStatusProps {
  cancelling: boolean;
  reservationPolicy?: ReservationPolicy;
}

export function SeatRegistrationStatus({
  cancelling,
  reservationPolicy = "notify_only",
}: SeatRegistrationStatusProps) {
  const activeDescription = reservationPolicy === "reserve_once_before_payment"
    ? "좌석 재발견마다 자동 예매 · 결제 전 중단"
    : "좌석 변화를 감시 중";
  return (
    <div className={cancelling ? "seat-registration-status is-cancelling" : "seat-registration-status"} role="status">
      <CheckCircle size={14} weight="fill" aria-hidden="true" />
      <span>
        <strong>{cancelling ? "대기 취소 중" : "대기 등록됨"}</strong>
        <small>{cancelling ? "등록 해제를 처리하고 있습니다" : activeDescription}</small>
      </span>
    </div>
  );
}

export interface SeatRegistrationCancelButtonProps {
  seatClassLabel: string;
  cancelling: boolean;
  onCancel: () => void;
}

export function SeatRegistrationCancelButton({
  seatClassLabel,
  cancelling,
  onCancel,
}: SeatRegistrationCancelButtonProps) {
  return (
    <button
      type="button"
      aria-pressed="true"
      aria-busy={cancelling}
      disabled={cancelling}
      className="button compact seat-action-cancel"
      onClick={onCancel}
    >
      <Trash size={16} weight="bold" aria-hidden="true" />
      {cancelling ? `${seatClassLabel} 취소 중…` : `${seatClassLabel} 대기 취소`}
    </button>
  );
}
