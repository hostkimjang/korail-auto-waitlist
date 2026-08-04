from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from rail_waitlist.domain import Provider, SeatClass
from rail_waitlist.korail_reservation_confirmation import (
    KorailSameSessionDetailEvidence,
    normalize_korail_same_session_detail,
)
from rail_waitlist.reservation_confirmation import (
    ReservationConfirmationOutcome,
    ReservationConfirmationTarget,
    require_official_handoff_url,
)
from rail_waitlist.srt_reservation_confirmation import (
    SRT_RESERVE_RESULT_SOURCE,
    SrtReservationListEvidence,
    SrtReservationRecord,
    normalize_srt_reservation_records,
    normalize_srt_reserve_result,
)

KOREA = ZoneInfo("Asia/Seoul")


def target(
    provider: Provider = Provider.SRT,
    credential_version: int = 7,
) -> ReservationConfirmationTarget:
    return ReservationConfirmationTarget(
        attempt_id="attempt-1",
        candidate_id="candidate-1",
        provider=provider,
        train_number="00329",
        origin="대전",
        destination="부산",
        departure_at=datetime(2026, 8, 3, 13, 9, tzinfo=KOREA),
        seat_class=SeatClass.STANDARD,
        passenger_count=1,
        credential_version=credential_version,
    )


def srt_record(
    *,
    train_number: str = "329",
    departure_date: str = "20260803",
    departure_time: str = "130900",
    origin: str = "대전",
    destination: str = "부산",
    payment_date: str = "20260803",
    payment_time: str = "235900",
    paid: bool = False,
    seat_class: SeatClass | None = SeatClass.STANDARD,
    passenger_count: int | None = 1,
) -> SrtReservationRecord:
    return SrtReservationRecord(
        train_number=train_number,
        departure_date=departure_date,
        departure_time=departure_time,
        origin=origin,
        destination=destination,
        payment_date=payment_date,
        payment_time=payment_time,
        paid=paid,
        seat_class=seat_class,
        passenger_count=passenger_count,
    )


def evidence(
    *,
    observed_at: datetime = datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
    credential_version: int | None = 7,
    records: tuple[SrtReservationRecord, ...] = (srt_record(),),
    auth_required: bool = False,
    provider_blocked: bool = False,
) -> SrtReservationListEvidence:
    return SrtReservationListEvidence(
        observed_at=observed_at,
        credential_version=credential_version,
        records=records,
        auth_required=auth_required,
        provider_blocked=provider_blocked,
    )


def test_srt_exact_unpaid_record_confirms_payment_hold_with_aware_deadline() -> None:
    result = normalize_srt_reservation_records(target(), evidence())

    assert result.outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
    assert result.payment_deadline == datetime(2026, 8, 3, 23, 59, tzinfo=KOREA)
    assert result.official_handoff_url == (
        "https://etk.srail.kr/hpg/hra/02/selectReservationList.do?pageId=TK0102010000"
    )
    assert not result.permits_automatic_reservation_retry


@pytest.mark.parametrize(
    "records",
    [
        (),
        (srt_record(train_number="330"),),
        (srt_record(origin="서울"),),
    ],
)
def test_srt_not_found_never_authorizes_another_reservation(
    records: tuple[SrtReservationRecord, ...],
) -> None:
    result = normalize_srt_reservation_records(target(), evidence(records=records))

    assert result.outcome is ReservationConfirmationOutcome.NOT_FOUND
    assert not result.permits_automatic_reservation_retry
    assert result.payment_deadline is None
    assert result.official_handoff_url is None


@pytest.mark.parametrize(
    "records",
    [
        (srt_record(), srt_record()),
        (srt_record(paid=True),),
    ],
)
def test_srt_ambiguous_or_paid_matches_fail_closed(
    records: tuple[SrtReservationRecord, ...],
) -> None:
    result = normalize_srt_reservation_records(target(), evidence(records=records))

    assert result.outcome is ReservationConfirmationOutcome.INCONCLUSIVE
    assert not result.permits_automatic_reservation_retry


@pytest.mark.parametrize(
    "record",
    [
        srt_record(seat_class=None),
        srt_record(seat_class=SeatClass.FIRST),
        srt_record(passenger_count=None),
        srt_record(passenger_count=2),
    ],
)
def test_srt_unproven_seat_class_or_passenger_count_is_inconclusive(
    record: SrtReservationRecord,
) -> None:
    result = normalize_srt_reservation_records(target(), evidence(records=(record,)))

    assert result.outcome is ReservationConfirmationOutcome.INCONCLUSIVE
    assert not result.permits_automatic_reservation_retry


def test_srt_generation_authentication_and_protection_boundaries_do_not_confirm() -> None:
    generation_mismatch = normalize_srt_reservation_records(
        target(),
        evidence(credential_version=8),
    )
    authentication = normalize_srt_reservation_records(target(), evidence(auth_required=True))
    blocked = normalize_srt_reservation_records(target(), evidence(provider_blocked=True))

    assert generation_mismatch.outcome is ReservationConfirmationOutcome.INCONCLUSIVE
    assert authentication.outcome is ReservationConfirmationOutcome.AUTH_REQUIRED
    assert blocked.outcome is ReservationConfirmationOutcome.PROVIDER_BLOCKED
    assert all(
        not result.permits_automatic_reservation_retry
        for result in (generation_mismatch, authentication, blocked)
    )


def test_srt_reserve_result_uses_the_same_exact_match_normalizer_without_a_second_call() -> None:
    result = normalize_srt_reserve_result(
        target(),
        srt_record(),
        observed_at=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
        credential_version=7,
    )

    assert result.outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
    assert result.source == SRT_RESERVE_RESULT_SOURCE


@pytest.mark.parametrize(
    "record",
    [
        srt_record(seat_class=None),
        srt_record(seat_class=SeatClass.FIRST),
        srt_record(passenger_count=None),
        srt_record(passenger_count=2),
    ],
)
def test_srt_reserve_result_does_not_replace_unproven_actual_values_with_target(
    record: SrtReservationRecord,
) -> None:
    result = normalize_srt_reserve_result(
        target(),
        record,
        observed_at=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
        credential_version=7,
    )

    assert result.outcome is ReservationConfirmationOutcome.INCONCLUSIVE
    assert not result.permits_automatic_reservation_retry


def test_korail_same_session_detail_confirms_only_exact_current_generation_evidence() -> None:
    result = normalize_korail_same_session_detail(
        target(Provider.KORAIL),
        KorailSameSessionDetailEvidence(
            observed_at=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
            credential_version=7,
            exact_identity_matched=True,
            payment_pending_markers_present=True,
            seat_class_matched=True,
            passenger_count_matched=True,
        ),
    )

    assert result.outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
    assert result.official_handoff_url == "https://www.korail.com/ticket/reservation/list"


def test_korail_reservation_list_confirms_without_claiming_seat_class_observation() -> None:
    result = normalize_korail_same_session_detail(
        target(Provider.KORAIL),
        KorailSameSessionDetailEvidence(
            observed_at=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
            credential_version=7,
            exact_identity_matched=True,
            payment_pending_markers_present=True,
            seat_class_matched=False,
            seat_class_match_required=False,
            passenger_count_matched=True,
            source="korail-reservation-list",
        ),
    )

    assert result.outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
    assert result.source == "korail-reservation-list"
    assert result.payment_deadline is None
    assert not result.permits_automatic_reservation_retry


def test_korail_completed_official_list_absence_is_not_found_without_retry_permission() -> None:
    result = normalize_korail_same_session_detail(
        target(Provider.KORAIL),
        KorailSameSessionDetailEvidence(
            observed_at=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
            credential_version=7,
            exact_identity_matched=False,
            payment_pending_markers_present=False,
            official_list_read_completed=True,
            official_list_target_absent=True,
            source="korail-reservation-list",
        ),
    )

    assert result.outcome is ReservationConfirmationOutcome.NOT_FOUND
    assert result.source == "korail-reservation-list"
    assert not result.permits_automatic_reservation_retry


def test_korail_uncertain_official_list_absence_remains_inconclusive() -> None:
    result = normalize_korail_same_session_detail(
        target(Provider.KORAIL),
        KorailSameSessionDetailEvidence(
            observed_at=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
            credential_version=7,
            exact_identity_matched=False,
            payment_pending_markers_present=False,
            official_list_read_completed=False,
            official_list_target_absent=False,
            source="korail-reservation-list",
        ),
    )

    assert result.outcome is ReservationConfirmationOutcome.INCONCLUSIVE


def test_korail_completed_official_list_with_ambiguous_matches_is_inconclusive() -> None:
    result = normalize_korail_same_session_detail(
        target(Provider.KORAIL),
        KorailSameSessionDetailEvidence(
            observed_at=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
            credential_version=7,
            exact_identity_matched=False,
            payment_pending_markers_present=False,
            official_list_read_completed=True,
            official_list_target_absent=False,
            source="korail-reservation-list",
        ),
    )

    assert result.outcome is ReservationConfirmationOutcome.INCONCLUSIVE


@pytest.mark.parametrize(
    "detail",
    [
        KorailSameSessionDetailEvidence(
            observed_at=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
            credential_version=7,
            exact_identity_matched=False,
            payment_pending_markers_present=True,
        ),
        KorailSameSessionDetailEvidence(
            observed_at=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
            credential_version=8,
            exact_identity_matched=True,
            payment_pending_markers_present=True,
            seat_class_matched=True,
            passenger_count_matched=True,
        ),
    ],
)
def test_korail_detail_absence_is_inconclusive_not_a_negative_reservation_lookup(
    detail: KorailSameSessionDetailEvidence,
) -> None:
    result = normalize_korail_same_session_detail(target(Provider.KORAIL), detail)

    assert result.outcome is ReservationConfirmationOutcome.INCONCLUSIVE
    assert not result.permits_automatic_reservation_retry


@pytest.mark.parametrize(
    ("seat_class_matched", "passenger_count_matched"),
    [(False, True), (True, False)],
)
def test_korail_unproven_seat_class_or_passenger_count_is_inconclusive(
    seat_class_matched: bool,
    passenger_count_matched: bool,
) -> None:
    result = normalize_korail_same_session_detail(
        target(Provider.KORAIL),
        KorailSameSessionDetailEvidence(
            observed_at=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
            credential_version=7,
            exact_identity_matched=True,
            payment_pending_markers_present=True,
            seat_class_matched=seat_class_matched,
            passenger_count_matched=passenger_count_matched,
        ),
    )

    assert result.outcome is ReservationConfirmationOutcome.INCONCLUSIVE


def test_korail_authentication_and_protection_evidence_has_priority() -> None:
    authentication = normalize_korail_same_session_detail(
        target(Provider.KORAIL),
        KorailSameSessionDetailEvidence(
            observed_at=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
            credential_version=7,
            exact_identity_matched=True,
            payment_pending_markers_present=True,
            seat_class_matched=True,
            passenger_count_matched=True,
            auth_required=True,
        ),
    )
    blocked = normalize_korail_same_session_detail(
        target(Provider.KORAIL),
        KorailSameSessionDetailEvidence(
            observed_at=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
            credential_version=7,
            exact_identity_matched=True,
            payment_pending_markers_present=True,
            seat_class_matched=True,
            passenger_count_matched=True,
            provider_blocked=True,
        ),
    )

    assert authentication.outcome is ReservationConfirmationOutcome.AUTH_REQUIRED
    assert blocked.outcome is ReservationConfirmationOutcome.PROVIDER_BLOCKED


def test_contract_is_redacted_and_handoff_hosts_are_provider_scoped() -> None:
    field_names = {field.name for field in fields(ReservationConfirmationTarget)}
    assert {"password", "cookie", "token", "reservation_number"}.isdisjoint(field_names)
    assert require_official_handoff_url(Provider.KORAIL, "https://www.korail.com/ticket/mypage")
    with pytest.raises(ValueError):
        require_official_handoff_url(Provider.KORAIL, "https://etk.srail.kr/hpg/hra/02/list")
    with pytest.raises(ValueError):
        require_official_handoff_url(Provider.SRT, "https://member:secret@etk.srail.kr/list")


def test_target_rejects_non_exact_or_unsafe_identity() -> None:
    with pytest.raises(ValueError, match="timezone"):
        ReservationConfirmationTarget(
            attempt_id="attempt-1",
            candidate_id="candidate-1",
            provider=Provider.SRT,
            train_number="329",
            origin="대전",
            destination="부산",
            departure_at=datetime(2026, 8, 3, 13, 9, tzinfo=UTC).replace(tzinfo=None),
            seat_class=SeatClass.STANDARD,
            passenger_count=1,
            credential_version=7,
        )
    with pytest.raises(ValueError, match="supported seat class"):
        ReservationConfirmationTarget(
            attempt_id="attempt-1",
            candidate_id="candidate-1",
            provider=Provider.SRT,
            train_number="329",
            origin="대전",
            destination="부산",
            departure_at=datetime(2026, 8, 3, 13, 9, tzinfo=KOREA),
            seat_class=SeatClass.ANY,
            passenger_count=1,
            credential_version=7,
        )
