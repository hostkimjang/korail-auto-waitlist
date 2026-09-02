from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .schemas import StationItem
from .station_names import normalize_korail_station_name


class StationCatalogSupplementConflict(ValueError):
    """A reviewed fallback no longer agrees with the current upstream identity."""


@dataclass(frozen=True)
class ReviewedTagoStationSupplement:
    node_id: str
    name: str
    city_code: str
    city_name: str
    korail_station_code: str

    def as_station_item(self) -> StationItem:
        return StationItem(
            node_id=self.node_id,
            name=self.name,
            city_code=self.city_code,
            city_name=self.city_name,
        )


# Verified against TAGO GetStrtpntAlocFndTrainInfo on 2026-09-02. TAGO accepts
# these identities and returns current trains, but omits them from its city/station
# catalog. KORAIL's current public station roster independently carries the paired
# four-digit station codes. Never add an unverified or guessed identity here.
REVIEWED_TAGO_STATION_SUPPLEMENTS = (
    ReviewedTagoStationSupplement(
        node_id="NATH30536",
        name="평택지제",
        city_code="31",
        city_name="경기도",
        korail_station_code="0553",
    ),
    ReviewedTagoStationSupplement(
        node_id="NAT023073",
        name="군위",
        city_code="22",
        city_name="대구광역시",
        korail_station_code="0548",
    ),
)


def apply_reviewed_tago_station_supplements(
    stations: Sequence[StationItem],
    official_station_codes: Mapping[str, str],
) -> list[StationItem]:
    """Add verified TAGO identities only while KORAIL lists the exact paired code."""

    supplemented = list(stations)
    official_codes_by_name = {
        normalize_korail_station_name(name): code.strip()
        for name, code in official_station_codes.items()
    }
    by_node_id = {station.node_id: station for station in stations}
    by_name = {normalize_korail_station_name(station.name): station for station in stations}

    for supplement in REVIEWED_TAGO_STATION_SUPPLEMENTS:
        normalized_name = normalize_korail_station_name(supplement.name)
        official_code = official_codes_by_name.get(normalized_name)
        if official_code is None:
            continue
        if official_code != supplement.korail_station_code:
            raise StationCatalogSupplementConflict(
                f"reviewed KORAIL station code changed: {supplement.name}"
            )
        existing_by_node = by_node_id.get(supplement.node_id)
        existing_by_name = by_name.get(normalized_name)
        if existing_by_node is None and existing_by_name is None:
            station = supplement.as_station_item()
            supplemented.append(station)
            by_node_id[station.node_id] = station
            by_name[normalized_name] = station
            continue
        if existing_by_node is existing_by_name and existing_by_node is not None:
            continue
        raise StationCatalogSupplementConflict(
            f"reviewed TAGO station supplement conflicts with upstream: {supplement.name}"
        )
    if len(supplemented) == len(stations):
        return supplemented
    return sorted(supplemented, key=lambda station: (station.name, station.node_id))
