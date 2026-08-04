from datetime import UTC, datetime, timedelta

from rail_waitlist.domain import Provider, SeatClass
from rail_waitlist.schemas import (
    SeatAvailabilityAction,
    SeatAvailabilityProvenance,
    SeatClassAvailability,
    TimetableItem,
)
from rail_waitlist.watch_registration_policy import apply_watch_registration_capability


def timetable_item() -> TimetableItem:
    departure = datetime(2030, 8, 1, 8, 30, tzinfo=UTC)
    observed_at = datetime.now(UTC).replace(microsecond=0)
    return TimetableItem(
        provider=Provider.KORAIL,
        train_number="26",
        train_type="KTX",
        origin="대전",
        destination="서울",
        departure_at=departure,
        arrival_at=departure + timedelta(hours=1),
        timetable_source="TAGO",
        timetable_retrieved_at=observed_at,
        official_booking_url="https://www.korail.com/ticket/search",
        seat_classes=[
            SeatClassAvailability(
                seat_class=SeatClass.STANDARD,
                status="sold_out",
                provenance=SeatAvailabilityProvenance(
                    kind="official_provider",
                    source="korail-official-page-browser",
                    observed_at=observed_at,
                ),
                actions=[SeatAvailabilityAction(kind="add_to_watch")],
                registration_evidence_id="00000000-0000-4000-8000-000000000001",
            ),
            SeatClassAvailability(
                seat_class=SeatClass.FIRST,
                status="available",
                provenance=SeatAvailabilityProvenance(
                    kind="official_provider",
                    source="korail-official-page-browser",
                    observed_at=observed_at,
                ),
                actions=[
                    SeatAvailabilityAction(
                        kind="official_check",
                        url="https://www.korail.com/ticket/search",
                    )
                ],
            ),
        ],
    )


def test_disabled_monitoring_preserves_status_but_removes_watch_registration() -> None:
    source = timetable_item()

    result = apply_watch_registration_capability(
        [source], seat_monitoring_enabled=False
    )[0]

    standard, first = result.seat_classes
    assert standard.status == "sold_out"
    assert standard.provenance.source == "korail-official-page-browser"
    assert standard.actions == []
    assert standard.registration_evidence_id is None
    assert [action.kind for action in first.actions] == ["official_check"]
    assert source.seat_classes[0].registration_evidence_id is not None


def test_enabled_monitoring_preserves_watch_registration_identity() -> None:
    source = timetable_item()
    items = [source]

    result = apply_watch_registration_capability(
        items, seat_monitoring_enabled=True
    )

    assert result is items
    assert result[0] is source
    assert result[0].seat_classes[0].registration_evidence_id == (
        "00000000-0000-4000-8000-000000000001"
    )
