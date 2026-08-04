from rail_waitlist.korail_reservation_controls import booking_seat_control_key


def test_accepts_price_only_anchor_owned_by_requested_seat_box() -> None:
    assert booking_seat_control_key(
        seat_class_label="일반실",
        control_text="23,700원",
        price_box_text="일반실 23,700원",
        price_box_classes=("price_box", "gen"),
    ) == "일반실 23,700원"


def test_accepts_limited_seat_instead_of_treating_sold_out_soon_as_sold_out() -> None:
    assert booking_seat_control_key(
        seat_class_label="일반실",
        control_text="일반실 23,700원",
        price_box_text="일반실 매진임박 23,700원",
        price_box_classes=("price_box", "sold_out_soon"),
    ) == "일반실 매진임박 23,700원"


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
