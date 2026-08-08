import json
import subprocess
import sys

from rail_waitlist.korail_reservation_controls import (
    booking_seat_control_key as legacy_booking_seat_control_key,
)
from rail_waitlist.provider_adapters.korail_reservation_controls import (
    booking_seat_control_key,
)


def test_legacy_booking_control_symbol_is_the_exact_canonical_object() -> None:
    assert legacy_booking_seat_control_key is booking_seat_control_key
    assert booking_seat_control_key.__module__ == (
        "rail_waitlist.provider_adapters.korail_reservation_controls"
    )


def test_accepts_price_only_anchor_owned_by_requested_seat_box() -> None:
    assert (
        booking_seat_control_key(
            seat_class_label="일반실",
            control_text="23,700원",
            price_box_text="일반실 23,700원",
            price_box_classes=("price_box", "gen"),
        )
        == "일반실 23,700원"
    )


def test_accepts_limited_seat_instead_of_treating_sold_out_soon_as_sold_out() -> None:
    assert (
        booking_seat_control_key(
            seat_class_label="일반실",
            control_text="일반실 23,700원",
            price_box_text="일반실 매진임박 23,700원",
            price_box_classes=("price_box", "sold_out_soon"),
        )
        == "일반실 매진임박 23,700원"
    )


def test_rejects_sold_out_waitlist_wrong_class_and_non_price_controls() -> None:
    cases = (
        {
            "control_text": "23,700원",
            "price_box_text": "일반실 매진 23,700원",
            "price_box_classes": ("price_box", "sold_out"),
        },
        {
            "control_text": "예약대기",
            "price_box_text": "일반실 예약대기",
            "price_box_classes": ("price_box",),
        },
        {
            "control_text": "33,200원",
            "price_box_text": "특실 33,200원",
            "price_box_classes": ("price_box",),
        },
        {
            "control_text": "일반실 23,700원",
            "price_box_text": "특실 33,200원",
            "price_box_classes": ("price_box",),
        },
        {
            "control_text": "예매",
            "price_box_text": "일반실 예매 가능",
            "price_box_classes": ("price_box",),
        },
        {
            "control_text": "할인 안내",
            "price_box_text": "일반실 23,700원 할인 안내",
            "price_box_classes": ("price_box",),
        },
    )

    for case in cases:
        assert booking_seat_control_key(seat_class_label="일반실", **case) is None


def test_booking_control_import_orders_preserve_exact_identity() -> None:
    script = r"""
import json
import sys

order = sys.argv[1]
if order == "canonical-first":
    from rail_waitlist.provider_adapters.korail_reservation_controls import (
        booking_seat_control_key as Canonical,
    )
    from rail_waitlist.korail_reservation_controls import (
        booking_seat_control_key as Legacy,
    )
else:
    from rail_waitlist.korail_reservation_controls import (
        booking_seat_control_key as Legacy,
    )
    from rail_waitlist.provider_adapters.korail_reservation_controls import (
        booking_seat_control_key as Canonical,
    )

print(json.dumps({
    "same": Legacy is Canonical,
    "module": Canonical.__module__,
}))
"""

    for order in ("canonical-first", "legacy-first"):
        completed = subprocess.run(
            [sys.executable, "-W", "error", "-c", script, order],
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(completed.stdout) == {
            "same": True,
            "module": "rail_waitlist.provider_adapters.korail_reservation_controls",
        }
