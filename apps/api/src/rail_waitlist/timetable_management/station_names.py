from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from types import MappingProxyType

# These pairs are evidence-backed name equivalences between current or historical
# public railway catalogs. Parentheses are not removed generically because names
# such as 판교(경기) and 판교(충남) identify different stations.
KORAIL_STATION_NAME_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "김천(구미)": "김천구미",
        "여수엑스포": "여수expo",
        "신경주": "경주",
        "울산": "울산(통도사)",
        "진부": "진부(오대산)",
    }
)


def normalize_korail_station_name(value: str) -> str:
    normalized = "".join(unicodedata.normalize("NFKC", value).split()).casefold()
    normalized = normalized.removesuffix("역")
    return KORAIL_STATION_NAME_ALIASES.get(normalized, normalized)
