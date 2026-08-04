from __future__ import annotations

import re
from collections.abc import Iterable

from .korail_browser_automation import status_from_seat_box

_BOOKING_PRICE_PATTERN = re.compile(r"\d{1,3}(?:,\d{3})*\s*원")
_BOOKABLE_SEAT_STATUSES = frozenset({"available", "limited"})


def booking_seat_control_key(
    *,
    seat_class_label: str,
    control_text: object,
    price_box_text: object,
    price_box_classes: Iterable[object] = (),
) -> str | None:
    """Return a stable key when a live price control belongs to the requested class.

    KORAIL's responsive result row can put the class label on the surrounding
    ``.price_box`` while the nested anchor contains only the price.  Observation
    already classifies that surrounding box, so reservation must use the same
    boundary instead of requiring the anchor text to repeat the class label.

    The helper remains deliberately strict: the requested class must lead the
    normalized box label, an explicit won price must be present, and the official
    seat-box classifier must report an immediately bookable state.  Reservation
    waitlist and standing-only controls therefore remain outside this one-click
    booking path.
    """

    label = _normalize_text(seat_class_label)
    own_label = _normalize_text(control_text)
    box_label = _normalize_text(price_box_text)
    # When the control has an owning price box, that box is authoritative for
    # seat-class association.  Falling back to the anchor is reserved for the
    # older markup where no price-box metadata is available at all.
    candidate = box_label or own_label
    if (
        not candidate.startswith(label)
        or _BOOKING_PRICE_PATTERN.search(candidate) is None
        or _BOOKING_PRICE_PATTERN.search(own_label) is None
    ):
        return None

    status = status_from_seat_box(
        candidate,
        {str(value) for value in price_box_classes},
    )
    if status not in _BOOKABLE_SEAT_STATUSES:
        return None
    return candidate


def _normalize_text(value: object) -> str:
    return " ".join(str(value).split())
