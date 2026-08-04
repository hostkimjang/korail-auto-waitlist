import type { ReactElement } from "react";

import type { ActiveWatch } from "../features/home/ActiveWatchList";
import { copyTrainJourney } from "../features/new-wait/TrainResultCard";
import { OfficialHandoff } from "../features/official-handoff/OfficialHandoff";
import type { OfficialHandoffTrain } from "../features/official-handoff/OfficialHandoff";

export function activeWatchHandoffTrain(watch: ActiveWatch): OfficialHandoffTrain {
  const [routeOrigin = "", routeDestination = ""] = String(watch.route ?? "").split(" → ");
  const origin = watch.origin || routeOrigin;
  const destination = watch.destination || routeDestination;
  const travelDate = watch.travelDate || "";

  return {
    id: `active-watch-${watch.id}`,
    provider: watch.provider,
    origin,
    destination,
    name: watch.train,
    date: watch.date,
    departure_at: travelDate ? `${travelDate}T${watch.departure}:00+09:00` : "",
    departure: watch.departure,
    arrival: watch.arrival,
    seat_classes: [],
  };
}

export function renderHomeSeatFoundAction(watch: ActiveWatch): ReactElement {
  return (
    <OfficialHandoff
      train={activeWatchHandoffTrain(watch)}
      selectedSeatClass={watch.seatClass}
      onCopy={copyTrainJourney}
      triggerLabel="예매"
      actionUrl={watch.officialBookingUrl ?? null}
      seatFoundObservation={watch.seatFoundObservation ?? null}
      triggerClassName="button button-primary compact watch-booking-button"
    />
  );
}
