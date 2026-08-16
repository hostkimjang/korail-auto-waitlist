from __future__ import annotations

from datetime import UTC, date, datetime, time, timezone

import pytest
from pydantic import ValidationError

from rail_waitlist.domain import Provider, ReservationOutcome, SeatClass
from rail_waitlist.korail_search_bootstrap import (
    KorailStationIdentity,
    build_korail_general_search_url,
)
from rail_waitlist.schemas import (
    ReservationProgressStage,
    ReservationRequest,
    ReservationResult,
    SeatAvailabilityAction,
    SeatAvailabilityProvenance,
    SeatClassAvailability,
    SeatObservationRequest,
    SeatObservationResult,
    TimetableItem,
    WatchCandidateLatestReservationAttemptRead,
)


def observation_request_payload(**overrides):
    payload = {
        "provider": Provider.MOCK,
        "origin_node_id": "MOCK-SEOUL",
        "destination_node_id": "MOCK-BUSAN",
        "origin": "서울",
        "destination": "부산",
        "train_number": "MOCK-001",
        "departure_at": "2026-08-01T09:00:00+09:00",
        "seat_class": SeatClass.STANDARD,
        "passenger_count": 1,
    }
    payload.update(overrides)
    return payload


def test_provider_contract_requests_are_typed_and_reject_transport_or_secret_fields():
    observation = SeatObservationRequest(**observation_request_payload())
    reservation = ReservationRequest(
        **observation_request_payload(),
        candidate_id="candidate-1",
        idempotency_key="reservation-attempt-1",
    )

    assert observation.provider == Provider.MOCK
    assert observation.origin == "서울"
    assert observation.destination == "부산"
    assert reservation.candidate_id == "candidate-1"
    for forbidden in ("raw_response", "headers", "cookie", "token"):
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            SeatObservationRequest(**observation_request_payload(**{forbidden: "secret"}))


@pytest.mark.parametrize(
    "overrides",
    [
        {"departure_at": "2026-08-01T09:00:00"},
        {"seat_class": SeatClass.ANY},
        {"destination_node_id": "MOCK-SEOUL"},
        {"destination": "서울"},
    ],
)
def test_observation_request_fails_closed_for_invalid_target(overrides):
    with pytest.raises(ValidationError):
        SeatObservationRequest(**observation_request_payload(**overrides))


def test_seat_observation_result_validates_source_status_time_and_freshness():
    result = SeatObservationResult(
        seat_class=SeatClass.STANDARD,
        status="available",
        source="mock",
        observed_at="2026-08-01T00:00:00Z",
        fresh_until="2026-08-01T00:05:00Z",
    )
    assert result.status == "available"

    invalid_payloads = [
        {"source": "   "},
        {"source": "https://private.example/source"},
        {"status": "maybe_available"},
        {"observed_at": "2026-08-01T00:00:00"},
        {"fresh_until": "2026-07-31T23:59:00Z"},
    ]
    for overrides in invalid_payloads:
        payload = {
            "seat_class": SeatClass.STANDARD,
            "status": "available",
            "source": "mock",
            "observed_at": "2026-08-01T00:00:00Z",
            "fresh_until": "2026-08-01T00:05:00Z",
            **overrides,
        }
        with pytest.raises(ValidationError):
            SeatObservationResult(**payload)


def test_error_observation_requires_a_normalized_error_category():
    with pytest.raises(ValidationError, match="require an error_category"):
        SeatObservationResult(
            seat_class=SeatClass.STANDARD,
            status="error",
            source="mock",
            observed_at="2026-08-01T00:00:00Z",
            fresh_until="2026-08-01T00:05:00Z",
        )


def test_reservation_result_keeps_temporary_reservation_distinct_from_payment():
    result = ReservationResult(
        outcome="reserved",
        source="mock",
        observed_at="2026-08-01T00:00:00Z",
        payment_deadline="2026-08-01T00:20:00Z",
        official_handoff_url="https://example.invalid/mock-booking",
    )
    assert result.outcome == "reserved"
    assert "payment completion" in ReservationResult.model_fields["outcome"].description

    with pytest.raises(ValidationError, match="not payment completion"):
        ReservationResult(
            outcome="reserved",
            source="mock",
            observed_at="2026-08-01T00:00:00Z",
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://example.invalid/mock-booking",
        "https://evil.example/mock-booking",
        "https://user:password@example.invalid/mock-booking",
    ],
)
def test_reservation_result_rejects_unsafe_handoff_urls(url):
    with pytest.raises(ValidationError):
        ReservationResult(
            outcome="payment_required",
            source="mock",
            observed_at="2026-08-01T00:00:00Z",
            payment_deadline="2026-08-01T00:20:00Z",
            official_handoff_url=url,
        )


def test_reservation_result_rejects_invalid_source_outcome_timezone_and_raw_data():
    base = {
        "outcome": "not_available",
        "source": "mock",
        "observed_at": "2026-08-01T00:00:00Z",
    }
    for overrides in (
        {"source": "   "},
        {"outcome": "completed"},
        {"observed_at": "2026-08-01T00:00:00"},
        {"raw_response": {"token": "secret"}},
    ):
        with pytest.raises(ValidationError):
            ReservationResult(**(base | overrides))


def test_reservation_result_accepts_only_unique_chronological_progress_evidence():
    result = ReservationResult(
        outcome="not_available",
        source="korail-pydoll-reservation",
        observed_at="2026-08-01T00:00:03Z",
        progress_stages=(
            ReservationProgressStage(
                stage="authenticated_session_ready",
                occurred_at="2026-08-01T00:00:01Z",
            ),
            ReservationProgressStage(
                stage="target_rechecked",
                occurred_at="2026-08-01T00:00:02Z",
            ),
        ),
    )

    assert [stage.stage for stage in result.progress_stages] == [
        "authenticated_session_ready",
        "target_rechecked",
    ]
    for invalid_progress in (
        [
            {"stage": "target_rechecked", "occurred_at": "2026-08-01T00:00:02Z"},
            {"stage": "target_rechecked", "occurred_at": "2026-08-01T00:00:03Z"},
        ],
        [
            {"stage": "target_rechecked", "occurred_at": "2026-08-01T00:00:02Z"},
            {
                "stage": "authenticated_session_ready",
                "occurred_at": "2026-08-01T00:00:01Z",
            },
        ],
        [{"stage": "target_rechecked", "occurred_at": "2026-08-01T00:00:02"}],
    ):
        with pytest.raises(ValidationError):
            ReservationResult(
                outcome="not_available",
                source="korail-pydoll-reservation",
                observed_at="2026-08-01T00:00:03Z",
                progress_stages=invalid_progress,
            )


def test_reservation_result_rejects_unactionable_duplicate_and_excess_seats() -> None:
    base = {
        "source": "korail-pydoll-reservation",
        "observed_at": "2026-08-01T00:00:03Z",
    }
    with pytest.raises(ValidationError, match="seat data"):
        ReservationResult(
            **base,
            outcome="not_available",
            reserved_seats=[{"car_number": "4", "seat_number": "8A"}],
        )
    with pytest.raises(ValidationError, match="unique"):
        ReservationResult(
            **base,
            outcome="payment_required",
            official_handoff_url="https://www.korail.com/ticket/mypage/mykorail",
            reserved_seats=[
                {"car_number": "4", "seat_number": "8A"},
                {"car_number": "4", "seat_number": "8a"},
            ],
        )
    with pytest.raises(ValidationError):
        ReservationResult(
            **base,
            outcome="payment_required",
            official_handoff_url="https://www.korail.com/ticket/mypage/mykorail",
            reserved_seats=[
                {"car_number": str(index), "seat_number": "8A"} for index in range(1, 11)
            ],
        )


def test_latest_reservation_attempt_read_is_typed_and_normalizes_sqlite_datetimes():
    attempt = WatchCandidateLatestReservationAttemptRead(
        outcome=ReservationOutcome.NOT_AVAILABLE,
        confirmation_outcome="not_found",
        started_at=datetime(2026, 8, 1, 0, 0),  # noqa: DTZ001 - SQLite returns naive values.
        finished_at=datetime(2026, 8, 1, 0, 1),  # noqa: DTZ001 - SQLite returns naive values.
        post_deadline_reconciled_at=datetime(  # noqa: DTZ001 - SQLite returns naive values.
            2026, 8, 1, 0, 2
        ),
        retryable=True,
        manual_check_required=False,
        retry_condition="new_availability_episode",
    )

    assert attempt.started_at.tzinfo is UTC
    assert attempt.finished_at is not None and attempt.finished_at.tzinfo is UTC
    assert attempt.post_deadline_reconciled_at is not None
    assert attempt.post_deadline_reconciled_at.tzinfo is UTC

    with pytest.raises(ValidationError, match="retry_condition"):
        WatchCandidateLatestReservationAttemptRead(
            outcome=ReservationOutcome.FAILED,
            started_at="2026-08-01T00:00:00Z",
            finished_at="2026-08-01T00:01:00Z",
            retryable=False,
            manual_check_required=True,
            retry_condition="retry_immediately",
        )

    with pytest.raises(ValidationError, match="post_deadline_reconciled_at"):
        WatchCandidateLatestReservationAttemptRead(
            outcome=ReservationOutcome.PAYMENT_REQUIRED,
            confirmation_outcome="not_found",
            started_at="2026-08-01T00:00:00Z",
            finished_at="2026-08-01T00:01:00Z",
            post_deadline_reconciled_at="2026-08-01T00:02:00",
            retryable=True,
            manual_check_required=False,
            retry_condition="new_availability_episode",
        )


@pytest.mark.parametrize(
    "status",
    [
        "available",
        "limited",
        "standing_plus_seat",
        "standing_only",
        "not_enough_seats",
        "sold_out",
        "waitlist_available",
        "reservation_completed",
        "not_offered",
        "departed",
        "out_of_service",
    ],
)
def test_observed_seat_status_requires_observed_provenance(status):
    with pytest.raises(ValidationError, match="requires observed provider provenance"):
        SeatClassAvailability(
            seat_class=SeatClass.STANDARD,
            status=status,
            provenance={
                "kind": "not_observed",
                "reason": "public_api_not_available",
            },
        )


@pytest.mark.parametrize(
    ("provenance", "message"),
    [
        ({"kind": "official_provider", "observed_at": datetime.now(timezone.utc)}, "source"),
        ({"kind": "official_provider", "source": "partner-api"}, "observed_at"),
    ],
)
def test_official_provider_provenance_requires_source_and_observed_at(provenance, message):
    with pytest.raises(ValidationError, match=message):
        SeatClassAvailability(
            seat_class=SeatClass.FIRST,
            status="available",
            provenance=provenance,
        )


def test_official_positive_seat_status_accepts_complete_evidence():
    observed_at = datetime.now(timezone.utc)
    item = SeatClassAvailability(
        seat_class=SeatClass.FIRST,
        status="available",
        provenance={
            "kind": "official_provider",
            "source": "authorized-partner-api",
            "observed_at": observed_at,
        },
        fare=83_700,
        actions=[
            {
                "kind": "official_check",
                "url": "https://www.korail.com/ticket/search",
            }
        ],
    )

    assert item.provenance.observed_at == observed_at
    assert item.fare == 83_700
    assert item.fare_currency == "KRW"
    assert item.actions[0].url is not None


def test_per_class_fare_requires_observed_provenance():
    with pytest.raises(ValidationError, match="per-class fare requires observed"):
        SeatClassAvailability(
            seat_class=SeatClass.STANDARD,
            status="unknown",
            provenance={
                "kind": "not_observed",
                "reason": "public_api_not_available",
            },
            fare=59_800,
        )


def test_not_observed_provenance_is_fail_closed():
    with pytest.raises(ValidationError, match="must report unknown"):
        SeatClassAvailability(
            seat_class=SeatClass.STANDARD,
            status="error",
            provenance={
                "kind": "not_observed",
                "reason": "public_api_not_available",
            },
        )
    with pytest.raises(ValidationError, match="cannot contain observation evidence"):
        SeatAvailabilityProvenance(
            kind="not_observed",
            source="TAGO",
            reason="public_api_not_available",
        )


@pytest.mark.parametrize(
    "reason",
    [
        "public_api_not_available",
        "source_not_configured",
        "provider_access_restricted",
        "unsupported_route",
        "passenger_count_not_supported",
        "no_exact_match",
        "source_unavailable",
    ],
)
def test_not_observed_provenance_accepts_safe_diagnostic_reasons(reason):
    provenance = SeatAvailabilityProvenance(kind="not_observed", reason=reason)

    assert provenance.reason == reason


@pytest.mark.parametrize(
    "action",
    [
        {"kind": "official_check"},
        {"kind": "official_check", "url": "http://www.korail.com/ticket/search"},
        {"kind": "official_waitlist", "url": "http://etk.srail.kr/waitlist"},
        {"kind": "add_to_watch", "url": "https://www.korail.com/ticket/search"},
    ],
)
def test_seat_actions_reject_missing_insecure_or_unexpected_urls(action):
    with pytest.raises(ValidationError):
        SeatAvailabilityAction(**action)


def test_timetable_rejects_duplicate_or_any_seat_classes():
    base_seat = {
        "status": "unknown",
        "provenance": {
            "kind": "not_observed",
            "reason": "public_api_not_available",
        },
    }
    timetable = {
        "provider": "korail",
        "train_number": "101",
        "train_type": "KTX",
        "origin": "서울",
        "destination": "부산",
        "departure_at": "2026-08-01T09:00:00+09:00",
        "arrival_at": "2026-08-01T11:30:00+09:00",
        "timetable_source": "TAGO",
        "timetable_retrieved_at": "2026-07-29T00:00:00Z",
        "official_booking_url": "https://www.korail.com/ticket/search",
    }
    with pytest.raises(ValidationError, match="unique seat classes"):
        TimetableItem(
            **timetable,
            seat_classes=[
                {"seat_class": "standard", **base_seat},
                {"seat_class": "standard", **base_seat},
            ],
        )
    with pytest.raises(ValidationError, match="cannot use the any seat class"):
        SeatClassAvailability(seat_class="any", **base_seat)


def test_timetable_rejects_insecure_official_booking_url():
    with pytest.raises(ValidationError, match="must use HTTPS"):
        TimetableItem(
            provider="korail",
            train_number="101",
            train_type="KTX",
            origin="서울",
            destination="부산",
            departure_at="2026-08-01T09:00:00+09:00",
            arrival_at="2026-08-01T11:30:00+09:00",
            timetable_source="TAGO",
            timetable_retrieved_at="2026-07-29T00:00:00Z",
            official_booking_url="http://www.korail.com/ticket/search",
        )


@pytest.mark.parametrize("train_type", ["", "   ", "\t\n"])
def test_timetable_rejects_blank_train_type(train_type: str) -> None:
    with pytest.raises(ValidationError, match="train_type"):
        TimetableItem(
            provider="korail",
            train_number="101",
            train_type=train_type,
            origin="서울",
            destination="부산",
            departure_at="2026-08-01T09:00:00+09:00",
            arrival_at="2026-08-01T11:30:00+09:00",
            timetable_source="TAGO",
            timetable_retrieved_at="2026-07-29T00:00:00Z",
            official_booking_url="https://www.korail.com/ticket/search",
        )


def test_timetable_normalizes_verified_train_type_spacing() -> None:
    item = TimetableItem(
        provider="korail",
        train_number="101",
        train_type="  KTX   청룡  ",
        origin="서울",
        destination="부산",
        departure_at="2026-08-01T09:00:00+09:00",
        arrival_at="2026-08-01T11:30:00+09:00",
        timetable_source="TAGO",
        timetable_retrieved_at="2026-07-29T00:00:00Z",
        official_booking_url="https://www.korail.com/ticket/search",
    )

    assert item.train_type == "KTX 청룡"


def test_timetable_rejects_non_provider_official_hosts_for_booking_and_actions():
    timetable = {
        "provider": "korail",
        "train_number": "101",
        "train_type": "KTX",
        "origin": "서울",
        "destination": "부산",
        "departure_at": "2026-08-01T09:00:00+09:00",
        "arrival_at": "2026-08-01T11:30:00+09:00",
        "timetable_source": "TAGO",
        "timetable_retrieved_at": "2026-07-29T00:00:00Z",
    }
    with pytest.raises(ValidationError, match="provider's official host"):
        TimetableItem(**timetable, official_booking_url="https://evil.example/ticket")

    with pytest.raises(ValidationError, match="official seat action"):
        TimetableItem(
            **timetable,
            official_booking_url="https://www.korail.com/ticket/search",
            seat_classes=[
                {
                    "seat_class": "standard",
                    "status": "available",
                    "provenance": {
                        "kind": "official_provider",
                        "source": "authorized-test",
                        "observed_at": "2026-07-29T00:00:00Z",
                    },
                    "actions": [{"kind": "official_check", "url": "https://evil.example/ticket"}],
                }
            ],
        )


def test_timetable_accepts_strict_korail_search_url_and_rejects_it_for_srt():
    search_url = build_korail_general_search_url(
        origin=KorailStationIdentity("0001", "서울"),
        destination=KorailStationIdentity("0020", "부산"),
        travel_date=date(2026, 8, 1),
        departure_time=time(9),
    )
    base = {
        "train_number": "101",
        "train_type": "KTX",
        "origin": "서울",
        "destination": "부산",
        "departure_at": "2026-08-01T09:00:00+09:00",
        "arrival_at": "2026-08-01T11:30:00+09:00",
        "timetable_source": "official_provider",
        "timetable_retrieved_at": "2026-07-29T00:00:00Z",
    }

    item = TimetableItem(
        **base,
        provider="korail",
        official_booking_url="https://www.korail.com/ticket/search/general",
        official_search_url=search_url,
    )
    assert item.official_search_url is not None

    with pytest.raises(ValidationError, match="only KORAIL timetable items"):
        TimetableItem(
            **{**base, "train_type": "SRT"},
            provider="srt",
            official_booking_url="https://etk.srail.kr/hpg/hra/01/selectScheduleList.do",
            official_search_url=search_url,
        )
