export type StationSearchItem = {
  name: string;
  nodeId: string;
  cityName?: string;
};

const koreanCollator = new Intl.Collator("ko-KR", {
  numeric: true,
  sensitivity: "base",
});

function normalizedSearchText(value: string): string {
  return value
    .normalize("NFKC")
    .replace(/\p{White_Space}+/gu, "")
    .toLocaleLowerCase("ko-KR");
}

function matchRank(station: StationSearchItem, query: string): number | null {
  const name = normalizedSearchText(station.name);
  const city = normalizedSearchText(station.cityName ?? "");

  if (name === query) return 0;
  if (name.startsWith(query)) return 1;
  if (name.includes(query)) return 2;
  if (city === query) return 3;
  if (city.startsWith(query)) return 4;
  if (city.includes(query)) return 5;
  return null;
}

export function rankedStationOptions<T extends StationSearchItem>(
  stations: ReadonlyArray<T>,
  rawQuery: string,
): Array<T> {
  const query = normalizedSearchText(rawQuery);
  if (!query) return [...stations];

  return stations
    .map((station) => ({ station, rank: matchRank(station, query) }))
    .filter((item): item is { station: T; rank: number } => item.rank !== null)
    .sort((left, right) => (
      left.rank - right.rank
      || koreanCollator.compare(left.station.name, right.station.name)
      || koreanCollator.compare(left.station.cityName ?? "", right.station.cityName ?? "")
      || koreanCollator.compare(left.station.nodeId, right.station.nodeId)
    ))
    .map(({ station }) => station);
}
