from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from importlib import import_module
from types import MappingProxyType

from ..timetable_management.schemas import StationItem

ROSTER_SOURCE = "srtrain-2.6.7-station-code+sr-official-cross-operation"
_REQUIRED_SENTINELS = frozenset({"수서", "대전", "부산"})
# SR announced and started SRT Seoul-Busan cross-operation on 2026-02-25, but
# SRTrain 2.6.7 predates that route and rejects Seoul before issuing the normal
# official timetable request. Keep this as a query-code extension, not an
# operator-membership filter: the selected service date's live result remains
# the only authority for whether an SRT train actually serves the route.
_OFFICIAL_CROSS_OPERATION_STATION_CODES = {"서울": "0001"}
_STATION_ALIASES = {
    "김천구미": "김천(구미)",
    "신경주": "경주",
    "여수엑스포": "여수expo",
    "울산": "울산(통도사)",
    "진부": "진부(오대산)",
}


class SrtStationRosterUnavailable(RuntimeError):
    pass


def normalize_srt_station_name(value: str) -> str:
    normalized = "".join(value.split()).removesuffix("역")
    normalized = normalized.casefold()
    return _STATION_ALIASES.get(normalized, normalized)


@dataclass(frozen=True)
class SrtStationRoster:
    canonical_names: Mapping[str, str]
    station_codes: Mapping[str, str]
    source: str = ROSTER_SOURCE

    @classmethod
    def from_station_codes(cls, station_codes: object) -> SrtStationRoster:
        if not isinstance(station_codes, Mapping) or not station_codes:
            raise SrtStationRosterUnavailable("SRT station roster is unavailable")
        canonical_names: dict[str, str] = {}
        normalized_codes: dict[str, str] = {}
        for raw_name, raw_code in station_codes.items():
            if not isinstance(raw_name, str) or not isinstance(raw_code, str):
                raise SrtStationRosterUnavailable("SRT station roster is invalid")
            name = raw_name.strip()
            code = raw_code.strip()
            normalized = normalize_srt_station_name(name)
            if not name or not code or not normalized:
                raise SrtStationRosterUnavailable("SRT station roster is invalid")
            existing_code = normalized_codes.get(normalized)
            if existing_code is not None and existing_code != code:
                raise SrtStationRosterUnavailable(
                    "SRT station roster has conflicting normalized station codes"
                )
            canonical_names[normalized] = name
            normalized_codes[normalized] = code
        for name, code in _OFFICIAL_CROSS_OPERATION_STATION_CODES.items():
            normalized = normalize_srt_station_name(name)
            existing_code = normalized_codes.get(normalized)
            if existing_code is not None and existing_code != code:
                raise SrtStationRosterUnavailable("SRT station roster extension conflicts")
            canonical_names[normalized] = name
            normalized_codes[normalized] = code
        if not _REQUIRED_SENTINELS.issubset(canonical_names):
            raise SrtStationRosterUnavailable("SRT station roster sentinels are missing")
        return cls(
            canonical_names=MappingProxyType(canonical_names),
            station_codes=MappingProxyType(normalized_codes),
        )

    def provider_name(self, value: str) -> str | None:
        return self.canonical_names.get(normalize_srt_station_name(value))

    def station_code(self, value: str) -> str | None:
        return self.station_codes.get(normalize_srt_station_name(value))

    def supports_route(self, origin: str, destination: str) -> bool:
        normalized_origin = normalize_srt_station_name(origin)
        normalized_destination = normalize_srt_station_name(destination)
        return (
            normalized_origin != normalized_destination
            and normalized_origin in self.canonical_names
            and normalized_destination in self.canonical_names
        )

    def filter_stations(self, stations: Sequence[StationItem]) -> list[StationItem]:
        return [station for station in stations if self.provider_name(station.name) is not None]


@lru_cache(maxsize=1)
def load_srt_station_roster() -> SrtStationRoster:
    try:
        constants = import_module("SRT.constants")
        station_codes = getattr(constants, "STATION_CODE")
    except (AttributeError, ImportError) as error:
        raise SrtStationRosterUnavailable("SRT station roster cannot be loaded") from error
    return SrtStationRoster.from_station_codes(station_codes)
