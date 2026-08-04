import type { KorailRenderedResultInput, KorailSnapshotPayload, SeatStatus } from "./types";

const KOREA_OFFSET = "+09:00";

export interface ParserInput {
  travelDate: string;
  passengerCount: number;
  rows: readonly KorailRenderedResultInput[];
}

function normalizeText(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function normalizeStation(value: string): string | null {
  const normalized = normalizeText(value).replace(/역$/, "");
  return normalized.length > 0 && normalized.length <= 40 ? normalized : null;
}

function normalizeTrainNumber(value: string): string | null {
  const normalized = normalizeText(value).replace(/[^0-9A-Za-z-]/g, "");
  return normalized.length > 0 && normalized.length <= 40 ? normalized : null;
}

function departureTimestamp(travelDate: string, time: string): string | null {
  const matched = /^(\d{2}):(\d{2})$/.exec(normalizeText(time));
  if (!/^\d{4}-\d{2}-\d{2}$/.test(travelDate) || matched === null) {
    return null;
  }
  const hours = Number(matched[1]);
  const minutes = Number(matched[2]);
  if (hours > 23 || minutes > 59) {
    return null;
  }
  return `${travelDate}T${matched[1]}:${matched[2]}:00${KOREA_OFFSET}`;
}

export function statusFromSeatBox(
  text: string,
  classNames: readonly string[],
): SeatStatus | null {
  const normalized = normalizeText(text).toLocaleLowerCase("ko-KR");
  const classes = classNames.map((className) => className.toLocaleLowerCase("en-US"));
  if (/예약\s*대기/.test(normalized)) {
    return "waitlist_available";
  }
  if (classes.includes("sold_out_soon") || /매진\s*임박/.test(normalized)) {
    return "limited";
  }
  if (classes.includes("sold_out") || /매진/.test(normalized)) {
    return "sold_out";
  }
  if (/입석\s*\+\s*(?:좌석|예매)/.test(normalized)) {
    return "standing_plus_seat";
  }
  if (
    !normalized
    || /^(?:일반실|특실)?\s*[-–—]\s*$/.test(normalized)
    || /(?:좌석\s*)?(?:없음|없습니다)|해당\s*없음|미운행|미운영|제공\s*안\s*함/.test(normalized)
  ) {
    return "not_offered";
  }
  if (/(?:예매|예약)\s*불가/.test(normalized)) {
    return null;
  }
  if (
    /\d{1,3}(?:,\d{3})*\s*원/.test(normalized)
    || /(?:예매|예약)\s*가능/.test(normalized)
    || /(?:^|\s)(?:예매|예약)(?:\s|$)/.test(normalized)
  ) {
    return "available";
  }
  return null;
}

export function parseKorailSnapshot(input: ParserInput): KorailSnapshotPayload | null {
  if (input.passengerCount !== 1 || !/^\d{4}-\d{2}-\d{2}$/.test(input.travelDate)) {
    return null;
  }

  const parsedTrains: KorailSnapshotPayload["trains"] = [];
  let route: { origin: string; destination: string } | null = null;
  const seenTrainNumbers = new Set<string>();

  for (const row of input.rows) {
    const trainNumber = normalizeTrainNumber(row.trainNumber);
    const origin = normalizeStation(row.origin);
    const destination = normalizeStation(row.destination);
    const departureAt = departureTimestamp(input.travelDate, row.departureTime);
    if (
      trainNumber === null ||
      origin === null ||
      destination === null ||
      origin === destination ||
      departureAt === null ||
      seenTrainNumbers.has(trainNumber)
    ) {
      return null;
    }

    if (route === null) {
      route = { origin, destination };
    } else if (route.origin !== origin || route.destination !== destination) {
      return null;
    }

    seenTrainNumbers.add(trainNumber);
    const standard = statusFromSeatBox(row.standardText, row.standardClassNames);
    const first = statusFromSeatBox(row.firstText, row.firstClassNames);
    if (standard === null || first === null) {
      return null;
    }
    parsedTrains.push({
      train_number: trainNumber,
      departure_at: departureAt,
      standard,
      first,
    });
  }

  if (route === null || parsedTrains.length === 0) {
    return null;
  }
  return {
    origin: route.origin,
    destination: route.destination,
    travel_date: input.travelDate,
    passenger_count: 1,
    trains: parsedTrains,
  };
}
