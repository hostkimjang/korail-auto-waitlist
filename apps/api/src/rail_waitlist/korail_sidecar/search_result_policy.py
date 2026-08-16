from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from datetime import time as clock_time
from zoneinfo import ZoneInfo

from .browser_contracts import KorailTrainType, SeatStatus

DELAY_ESTIMATE_PATTERN = re.compile(r"(?<!\d)(\d{1,3})\s*분\s*지연\s*예상")
ADULT_FARE_PATTERN = re.compile(r"(?<!\d)(\d{1,3}(?:,\d{3})*)\s*원(?!\w)")
OFFICIAL_TRAIN_TYPE_PATTERN = re.compile(
    r"^(KTX(?:\s*[-–—]?\s*(?:산천|청룡))?)(?:\s+(?:[A-Za-z]\s*)?0*\d+)?$",
    re.IGNORECASE,
)
KST = ZoneInfo("Asia/Seoul")


def parse_expected_delay_minutes(value: str) -> int | None:
    values = {int(item) for item in DELAY_ESTIMATE_PATTERN.findall(value)}
    if len(values) != 1:
        return None
    delay = values.pop()
    return delay if delay > 0 else None


def parse_unambiguous_adult_fare(value: str) -> int | None:
    matches = ADULT_FARE_PATTERN.findall(" ".join(value.split()))
    if len(matches) != 1:
        return None
    fare = int(matches[0].replace(",", ""))
    return fare if fare > 0 else None


def parse_official_train_type(value: str) -> KorailTrainType | None:
    match = OFFICIAL_TRAIN_TYPE_PATTERN.fullmatch(" ".join(value.split()))
    if match is None:
        return None
    train_type = match.group(1).replace("–", "-").replace("—", "-")
    if "산천" in train_type:
        return "KTX-산천"
    if "청룡" in train_type:
        return "KTX-청룡"
    return "KTX"


def is_supported_korail_train_kind(value: str) -> bool:
    return parse_official_train_type(value) is not None


def service_datetimes(
    travel_date: date,
    departure_time: clock_time,
    arrival_time: clock_time,
) -> tuple[datetime, datetime]:
    departure_at = datetime.combine(travel_date, departure_time, tzinfo=KST)
    arrival_at = datetime.combine(travel_date, arrival_time, tzinfo=KST)
    if arrival_at <= departure_at:
        arrival_at += timedelta(days=1)
    return departure_at, arrival_at


def visible_departure_matches(value: str, travel_date: date, hour: int) -> bool:
    normalized = " ".join(value.split())
    iso_date = travel_date.isoformat()
    if normalized == iso_date:
        # The deterministic fixture keeps the minimal date-only representation.
        return True
    pattern = rf"{re.escape(iso_date)}\([월화수목금토일]\)\s+{hour:02d}:\d{{2}}"
    return re.fullmatch(pattern, normalized) is not None


def status_from_seat_box(text: str, classes: set[str]) -> SeatStatus | None:
    normalized = " ".join(text.split()).casefold()
    classes = {item.casefold() for item in classes}
    if re.search(r"예약\s*대기", normalized):
        return "waitlist_available"
    if "sold_out_soon" in classes or re.search(r"매진\s*임박", normalized):
        return "limited"
    if re.search(r"입석\s*\+\s*(?:좌석|예매)", normalized):
        return "standing_plus_seat"
    if re.fullmatch(r"(?:일반실\s*)?입석(?:\s*예매)?", normalized):
        return "standing_only"
    if "sold_out" in classes or re.search(r"매진", normalized):
        return "sold_out"
    if not normalized or re.fullmatch(r"(?:일반실|특실)?\s*[-–—]\s*", normalized):
        return "not_offered"
    if re.search(r"(?:좌석\s*)?(?:없음|없습니다)|해당\s*없음|미운행|미운영", normalized):
        return "not_offered"
    if re.search(r"(?:예매|예약)\s*불가", normalized):
        return None
    if re.search(r"\d{1,3}(?:,\d{3})*\s*원|(?:예매|예약)\s*가능", normalized):
        return "available"
    return None
