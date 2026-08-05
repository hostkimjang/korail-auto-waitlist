import { Plus } from "@phosphor-icons/react";
import type { ReactElement } from "react";

import { PageHeader } from "../../shared/ui/PageHeader";
import { ReservationList } from "./ReservationList";
import { ReservationSummary } from "./ReservationSummary";
import type { ReservationWatchViewModel } from "./reservationViewModel";

export interface ReservationsPageProps {
  watches: ReadonlyArray<ReservationWatchViewModel>;
  onCreate: () => void;
  onDelete?: (watchId: string) => void;
}

const ignoreDelete = (_watchId: string): void => undefined;

function openOfficialReservation(watch: ReservationWatchViewModel): void {
  const officialUrl = watch.officialBookingUrl;
  if (!officialUrl) return;
  window.open(officialUrl, "_blank", "noopener,noreferrer");
}

export function ReservationsPage({
  watches,
  onCreate,
  onDelete = ignoreDelete,
}: ReservationsPageProps): ReactElement {
  return (
    <div className="page">
      <PageHeader
        title="내 예약"
        helper="감시부터 결제 완료까지 상태를 구분해 보여드립니다."
        action={(
          <button
            className="button button-primary compact"
            type="button"
            onClick={onCreate}
          >
            <Plus />새 대기
          </button>
        )}
      />
      <ReservationSummary watches={watches} />
      <ReservationList
        watches={watches}
        onCreate={onCreate}
        onOpenOfficial={openOfficialReservation}
        onDelete={onDelete}
      />
    </div>
  );
}
