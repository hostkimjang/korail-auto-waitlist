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

function normalizedStationSearchText(value: string): string {
  return normalizedSearchText(value).replace(/역$/u, "");
}

// This mirrors the API's reviewed station-name equivalences so users can search by
// either the current KORAIL display name or the previous TAGO/provider name.
export const REVIEWED_STATION_SEARCH_EQUIVALENCES = [
  ["김천(구미)", "김천구미"],
  ["여수엑스포", "여수EXPO"],
  ["신경주", "경주"],
  ["울산", "울산(통도사)"],
  ["진부", "진부(오대산)"],
] as const;

const explicitStationSearchAliases: Readonly<Record<string, ReadonlyArray<string>>> =
  Object.fromEntries(REVIEWED_STATION_SEARCH_EQUIVALENCES.flatMap(([left, right]) => [
    [normalizedStationSearchText(left), [right]],
    [normalizedStationSearchText(right), [left]],
  ]));

function matchRank(station: StationSearchItem, query: string): number | null {
  const name = normalizedStationSearchText(station.name);
  const city = normalizedSearchText(station.cityName ?? "");
  const aliases = (explicitStationSearchAliases[name] ?? []).map(normalizedStationSearchText);

  if (name === query) return 0;
  if (name.startsWith(query)) return 1;
  if (name.includes(query)) return 2;
  if (aliases.some((alias) => alias === query)) return 3;
  if (aliases.some((alias) => alias.startsWith(query))) return 4;
  if (aliases.some((alias) => alias.includes(query))) return 5;
  if (city === query) return 6;
  if (city.startsWith(query)) return 7;
  if (city.includes(query)) return 8;
  return null;
}

export function rankedStationOptions<T extends StationSearchItem>(
  stations: ReadonlyArray<T>,
  rawQuery: string,
): Array<T> {
  const query = normalizedStationSearchText(rawQuery);
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
