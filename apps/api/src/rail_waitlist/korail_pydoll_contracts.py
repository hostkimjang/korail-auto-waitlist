"""Secret-free read models shared by KORAIL Pydoll application actors and the DOM driver."""

from __future__ import annotations

import re
from dataclasses import dataclass

KORAIL_ROUTE_HEADING = re.compile(
    r"^(.+?)\s*→\s*(.+?)\s*\(\s*(\d{2}:\d{2})\s*~\s*(\d{2}:\d{2})\s*\)"
    r"(?:\s*소요시간\s*:\s*.+)?$"
)


@dataclass(frozen=True)
class PydollSeatBox:
    text: str
    classes: frozenset[str]


@dataclass(frozen=True)
class PydollTrainRow:
    kind_text: str
    train_number: str
    route_text: str
    seats: tuple[PydollSeatBox, ...]
    full_text: str = ""


@dataclass(frozen=True)
class PydollPageSnapshot:
    body_text: str
    rows: tuple[PydollTrainRow, ...]
    protection_texts: tuple[str, ...] = ()
    network_responses: tuple[tuple[int, str], ...] = ()
    url: str = ""
    title: str = ""
    reservation_rows: tuple[str, ...] = ()


def normalize_korail_station(value: str) -> str:
    return " ".join(value.split()).removesuffix("역")


def normalize_korail_train_number(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z-]", "", " ".join(value.split()))
    if not normalized or len(normalized) > 40:
        raise ValueError("KORAIL train number is required")
    digits = "".join(character for character in normalized if character.isdigit())
    return digits.lstrip("0") or "0"
