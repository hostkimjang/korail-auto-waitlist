from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import rail_waitlist.reservations.provider_confirmation.srt as canonical
import rail_waitlist.srt_reservation_confirmation as legacy
from rail_waitlist.domain import Provider, SeatClass
from rail_waitlist.reservation_confirmation import (
    ReservationConfirmationOutcome,
    ReservationConfirmationPurpose,
    ReservationConfirmationResult,
    ReservationConfirmationTarget,
)

API_ROOT = Path(__file__).resolve().parents[1]
KOREA = ZoneInfo("Asia/Seoul")
OBSERVED_AT = datetime(2026, 8, 7, 3, 0, tzinfo=UTC)
PUBLIC_SYMBOLS = (
    "SRT_RESERVATION_HANDOFF_URL",
    "SRT_RESERVATION_LIST_SOURCE",
    "SRT_RESERVE_RESULT_SOURCE",
    "SrtReadOnlyReservationListProbe",
    "SrtReservationListConfirmationAdapter",
    "SrtReservationListEvidence",
    "SrtReservationRecord",
    "normalize_srt_reservation_records",
    "normalize_srt_reserve_result",
)


def _target(
    *,
    provider: Provider = Provider.SRT,
    credential_version: int = 7,
    purpose: ReservationConfirmationPurpose = ReservationConfirmationPurpose.INITIAL,
) -> ReservationConfirmationTarget:
    return ReservationConfirmationTarget(
        attempt_id="attempt-1",
        candidate_id="candidate-1",
        provider=provider,
        train_number="00329",
        origin="대전",
        destination="부산",
        departure_at=datetime(2026, 8, 7, 13, 9, tzinfo=KOREA),
        seat_class=SeatClass.STANDARD,
        passenger_count=1,
        credential_version=credential_version,
        purpose=purpose,
    )


def _record(
    *,
    train_number: str = "329",
    departure_date: str = "20260807",
    departure_time: str = "130900",
    origin: str = "대전",
    destination: str = "부산",
    payment_date: str = "20260807",
    payment_time: str = "235900",
    paid: bool = False,
    seat_class: SeatClass | None = SeatClass.STANDARD,
    passenger_count: int | None = 1,
) -> canonical.SrtReservationRecord:
    return canonical.SrtReservationRecord(
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


def _evidence(
    *,
    observed_at: datetime = OBSERVED_AT,
    credential_version: int | None = 7,
    records: tuple[canonical.SrtReservationRecord, ...] = (_record(),),
    auth_required: bool = False,
    provider_blocked: bool = False,
) -> canonical.SrtReservationListEvidence:
    return canonical.SrtReservationListEvidence(
        observed_at=observed_at,
        credential_version=credential_version,
        records=records,
        auth_required=auth_required,
        provider_blocked=provider_blocked,
    )


def test_srt_confirmation_facade_exports_exact_canonical_objects() -> None:
    assert legacy.__all__ == PUBLIC_SYMBOLS
    for symbol in PUBLIC_SYMBOLS:
        assert getattr(legacy, symbol) is getattr(canonical, symbol)
    assert canonical.SrtReservationRecord.__module__ == (
        "rail_waitlist.reservations.provider_confirmation.srt"
    )
    assert canonical.normalize_srt_reservation_records.__module__ == (
        "rail_waitlist.reservations.provider_confirmation.srt"
    )


def test_srt_confirmation_dataclass_shapes_are_preserved() -> None:
    assert [field.name for field in fields(canonical.SrtReservationRecord)] == [
        "train_number",
        "departure_date",
        "departure_time",
        "origin",
        "destination",
        "payment_date",
        "payment_time",
        "paid",
        "seat_class",
        "passenger_count",
    ]
    assert [field.name for field in fields(canonical.SrtReservationListEvidence)] == [
        "observed_at",
        "credential_version",
        "records",
        "auth_required",
        "provider_blocked",
    ]
    assert canonical.SrtReservationRecord.__dataclass_params__.frozen
    assert canonical.SrtReservationListEvidence.__dataclass_params__.frozen
    assert canonical.SrtReservationRecord.__slots__
    assert canonical.SrtReservationListEvidence.__slots__
    assert canonical.SrtReservationListConfirmationAdapter.__slots__ == ("probe",)


def test_srt_confirmation_evidence_only_rejects_naive_observation_time() -> None:
    with pytest.raises(ValueError, match="timezone"):
        _evidence(observed_at=OBSERVED_AT.replace(tzinfo=None))

    contradictory = _evidence(auth_required=True, provider_blocked=True, records=())
    assert contradictory.auth_required
    assert contradictory.provider_blocked
    assert contradictory.records == ()


def test_srt_confirmation_rejects_non_srt_target_before_evidence_branches() -> None:
    with pytest.raises(ValueError, match="non-SRT target"):
        canonical.normalize_srt_reservation_records(
            _target(provider=Provider.KORAIL),
            _evidence(provider_blocked=True, auth_required=True),
        )


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        (
            _evidence(
                provider_blocked=True,
                auth_required=True,
                credential_version=8,
                records=(),
            ),
            ReservationConfirmationOutcome.PROVIDER_BLOCKED,
        ),
        (
            _evidence(auth_required=True, credential_version=8, records=()),
            ReservationConfirmationOutcome.AUTH_REQUIRED,
        ),
        (
            _evidence(credential_version=8, records=()),
            ReservationConfirmationOutcome.INCONCLUSIVE,
        ),
        (_evidence(records=()), ReservationConfirmationOutcome.NOT_FOUND),
        (
            _evidence(records=(_record(), _record())),
            ReservationConfirmationOutcome.INCONCLUSIVE,
        ),
        (
            _evidence(records=(_record(paid=True),)),
            ReservationConfirmationOutcome.CONFIRMED_PAID,
        ),
        (_evidence(), ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED),
    ],
)
def test_srt_confirmation_branch_precedence_is_preserved(
    evidence: canonical.SrtReservationListEvidence,
    expected: ReservationConfirmationOutcome,
) -> None:
    purpose = (
        ReservationConfirmationPurpose.PAYMENT_FOLLOW_UP
        if expected is ReservationConfirmationOutcome.CONFIRMED_PAID
        else ReservationConfirmationPurpose.INITIAL
    )
    result = canonical.normalize_srt_reservation_records(
        _target(purpose=purpose),
        evidence,
    )
    assert result.outcome is expected
    assert not result.permits_automatic_reservation_retry


@pytest.mark.parametrize(
    ("payment_date", "payment_time"),
    [("", ""), ("20260230", "1200"), ("20260807", "9999")],
)
def test_srt_confirmation_invalid_payment_deadline_remains_confirmed_without_deadline(
    payment_date: str,
    payment_time: str,
) -> None:
    result = canonical.normalize_srt_reservation_records(
        _target(),
        _evidence(records=(_record(payment_date=payment_date, payment_time=payment_time),)),
    )
    assert result.outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
    assert result.payment_deadline is None
    assert result.official_handoff_url == canonical.SRT_RESERVATION_HANDOFF_URL


def test_srt_confirmation_validates_current_handoff_url_at_call_time(monkeypatch) -> None:
    monkeypatch.setattr(canonical, "SRT_RESERVATION_HANDOFF_URL", "https://example.com/pay")
    with pytest.raises(ValueError, match="provider allowlist"):
        canonical.normalize_srt_reservation_records(_target(), _evidence())


class _Probe:
    def __init__(self, evidence: canonical.SrtReservationListEvidence) -> None:
        self.evidence = evidence
        self.targets: list[ReservationConfirmationTarget] = []

    async def list_reservations(
        self,
        target: ReservationConfirmationTarget,
    ) -> canonical.SrtReservationListEvidence:
        self.targets.append(target)
        return self.evidence


async def test_srt_confirmation_adapter_uses_probe_once_and_current_normalizer(
    monkeypatch,
) -> None:
    target = _target()
    evidence = _evidence()
    expected = ReservationConfirmationResult(
        provider=Provider.SRT,
        outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
        source=canonical.SRT_RESERVATION_LIST_SOURCE,
        observed_at=OBSERVED_AT,
    )
    probe = _Probe(evidence)
    calls: list[tuple[ReservationConfirmationTarget, object]] = []

    def normalize(
        received_target: ReservationConfirmationTarget,
        received_evidence: canonical.SrtReservationListEvidence,
        *,
        source: str = canonical.SRT_RESERVATION_LIST_SOURCE,
    ) -> ReservationConfirmationResult:
        assert source == canonical.SRT_RESERVATION_LIST_SOURCE
        calls.append((received_target, received_evidence))
        return expected

    monkeypatch.setattr(canonical, "normalize_srt_reservation_records", normalize)
    adapter = canonical.SrtReservationListConfirmationAdapter(probe)

    assert await adapter.confirm(target) is expected
    assert len(probe.targets) == 1
    assert probe.targets[0] is target
    assert len(calls) == 1
    assert calls[0][0] is target
    assert calls[0][1] is evidence


@pytest.mark.parametrize("stage", ["probe", "normalizer"])
async def test_srt_confirmation_adapter_propagates_original_errors(
    monkeypatch,
    stage: str,
) -> None:
    error = RuntimeError(f"{stage} failed")

    class FailingProbe:
        async def list_reservations(
            self,
            _target: ReservationConfirmationTarget,
        ) -> canonical.SrtReservationListEvidence:
            if stage == "probe":
                raise error
            return _evidence()

    def fail(*_args: object, **_kwargs: object) -> ReservationConfirmationResult:
        raise error

    if stage == "normalizer":
        monkeypatch.setattr(canonical, "normalize_srt_reservation_records", fail)
    adapter = canonical.SrtReservationListConfirmationAdapter(FailingProbe())

    with pytest.raises(RuntimeError) as caught:
        await adapter.confirm(_target())
    assert caught.value is error


def test_srt_reserve_result_uses_current_normalizer_with_a_redacted_record_copy(
    monkeypatch,
) -> None:
    target = _target()
    record = _record()
    expected = ReservationConfirmationResult(
        provider=Provider.SRT,
        outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
        source=canonical.SRT_RESERVE_RESULT_SOURCE,
        observed_at=OBSERVED_AT,
    )
    captured: dict[str, object] = {}

    def normalize(
        received_target: ReservationConfirmationTarget,
        received_evidence: canonical.SrtReservationListEvidence,
        *,
        source: str = canonical.SRT_RESERVATION_LIST_SOURCE,
    ) -> ReservationConfirmationResult:
        captured.update(target=received_target, evidence=received_evidence, source=source)
        return expected

    monkeypatch.setattr(canonical, "normalize_srt_reservation_records", normalize)

    assert (
        canonical.normalize_srt_reserve_result(
            target,
            record,
            observed_at=OBSERVED_AT,
            credential_version=7,
        )
        is expected
    )
    evidence = captured["evidence"]
    assert isinstance(evidence, canonical.SrtReservationListEvidence)
    assert captured["target"] is target
    assert captured["source"] == canonical.SRT_RESERVE_RESULT_SOURCE
    assert evidence.records == (record,)
    assert evidence.records[0] is not record


@pytest.mark.parametrize("import_order", ["canonical-first", "legacy-first", "reservation-first"])
def test_srt_confirmation_import_orders_keep_one_owner(import_order: str) -> None:
    script = r"""
import json
import sys

if sys.argv[1] == "canonical-first":
    import rail_waitlist.reservations.provider_confirmation.srt as canonical
elif sys.argv[1] == "legacy-first":
    import rail_waitlist.srt_reservation_confirmation as legacy
else:
    from rail_waitlist import srt_reservation as reservation

import rail_waitlist.reservations.provider_confirmation.srt as canonical
import rail_waitlist.srt_reservation_confirmation as legacy
from rail_waitlist import srt_reservation as reservation

print(json.dumps({
    "facade": all(getattr(legacy, name) is getattr(canonical, name) for name in legacy.__all__),
    "reservation": reservation.SrtReservationRecord is canonical.SrtReservationRecord,
    "record_module": canonical.SrtReservationRecord.__module__,
    "function_module": canonical.normalize_srt_reservation_records.__module__,
}, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-W", "error", "-c", script, import_order],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {
        "facade": True,
        "function_module": "rail_waitlist.reservations.provider_confirmation.srt",
        "record_module": "rail_waitlist.reservations.provider_confirmation.srt",
        "reservation": True,
    }
