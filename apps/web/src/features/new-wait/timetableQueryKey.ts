interface TimetableQueryForm {
  providers: string[];
  origin: string;
  origin_node_id: string | null;
  destination: string;
  destination_node_id: string | null;
  date: string;
  time: string;
  timeEnd: string;
  passengers: string;
}

export function buildTimetableQueryKey(form: TimetableQueryForm): string {
  return JSON.stringify({
    providers: [...form.providers].sort(),
    origin: form.origin.trim(),
    originNodeId: form.origin_node_id,
    destination: form.destination.trim(),
    destinationNodeId: form.destination_node_id,
    date: form.date,
    timeFrom: form.time,
    timeTo: form.timeEnd,
    passengerCount: Number(form.passengers),
  });
}
