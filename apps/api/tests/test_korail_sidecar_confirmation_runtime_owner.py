from __future__ import annotations

import ast
import base64
import pickle
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

import rail_waitlist.korail_browser_seat_source as legacy
from rail_waitlist.domain import Provider, SeatClass
from rail_waitlist.korail_sidecar.client import _AdapterFailure
from rail_waitlist.korail_sidecar.contracts import (
    KorailReservationConfirmationRequest,
    KorailReservationConfirmationResult,
)
from rail_waitlist.reservations.provider_confirmation import korail_sidecar_runtime as owner
from rail_waitlist.reservations.provider_confirmation.contracts import (
    ReservationConfirmationOutcome,
    ReservationConfirmationPurpose,
    ReservationConfirmationResult,
    ReservationConfirmationSeat,
    ReservationConfirmationTarget,
)

API_ROOT = Path(__file__).resolve().parents[1]
LEGACY_METHOD_PICKLE = (
    "gASVXAAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfc2VhdF9zb3VyY2WU"
    "jCtLb3JhaWxCcm93c2VyU2VhdFNvdXJjZS5jb25maXJtX3Jlc2VydmF0aW9ulJOULg=="
)
NOW = datetime(2026, 8, 8, 3, tzinfo=UTC)


def _target(
    *,
    provider: Provider = Provider.KORAIL,
    purpose: ReservationConfirmationPurpose = ReservationConfirmationPurpose.INITIAL,
    passenger_count: int = 1,
    reserved_seats: tuple[ReservationConfirmationSeat, ...] = (),
) -> ReservationConfirmationTarget:
    return ReservationConfirmationTarget(
        attempt_id="attempt-1",
        candidate_id="candidate-1",
        provider=provider,
        train_number="KTX 0043",
        origin="서울",
        destination="부산",
        departure_at=datetime.fromisoformat("2026-08-09T08:00:00+09:00"),
        arrival_at=datetime.fromisoformat("2026-08-09T10:30:00+09:00"),
        seat_class=SeatClass.STANDARD,
        passenger_count=passenger_count,
        credential_version=7,
        purpose=purpose,
        reserved_seats=reserved_seats,
    )


async def test_owner_preserves_exact_request_and_valid_result() -> None:
    requests: list[KorailReservationConfirmationRequest] = []
    clock_calls = 0
    wire_result = KorailReservationConfirmationResult(
        outcome="confirmed_payment_required",
        source="korail-reservation-list",
        observed_at=NOW,
        payment_deadline=datetime(2026, 8, 8, 4, tzinfo=UTC),
        official_handoff_url="https://www.korail.com/ticket/reservation/list",
    )

    async def confirm(
        request: KorailReservationConfirmationRequest,
    ) -> KorailReservationConfirmationResult:
        requests.append(request)
        return wire_result

    def now() -> datetime:
        nonlocal clock_calls
        clock_calls += 1
        return NOW

    result = await owner.confirm_korail_sidecar_reservation(
        enabled=True,
        target=_target(),
        confirm=confirm,
        normalize_train_number=lambda _value: "43",
        now=now,
        adapter_failure_type=_AdapterFailure,
    )

    assert requests == [
        KorailReservationConfirmationRequest(
            attempt_id="attempt-1",
            candidate_id="candidate-1",
            train_number="43",
            origin="서울",
            destination="부산",
            departure_at=_target().departure_at,
            arrival_at=_target().arrival_at,
            seat_class="standard",
            passenger_count=1,
            credential_version=7,
        )
    ]
    assert result == ReservationConfirmationResult(
        provider=Provider.KORAIL,
        outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
        source="korail-reservation-list",
        observed_at=NOW,
        payment_deadline=datetime(2026, 8, 8, 4, tzinfo=UTC),
        official_handoff_url="https://www.korail.com/ticket/reservation/list",
    )
    assert clock_calls == 0


@pytest.mark.parametrize(
    ("enabled", "provider"),
    [(False, Provider.KORAIL), (True, Provider.SRT)],
)
async def test_owner_fails_closed_before_transport_for_ineligible_targets(
    enabled: bool,
    provider: Provider,
) -> None:
    async def reject_call(
        _request: KorailReservationConfirmationRequest,
    ) -> KorailReservationConfirmationResult:
        raise AssertionError("ineligible confirmation must not reach the sidecar")

    result = await owner.confirm_korail_sidecar_reservation(
        enabled=enabled,
        target=_target(provider=provider),
        confirm=reject_call,
        normalize_train_number=lambda _value: "43",
        now=lambda: NOW,
        adapter_failure_type=_AdapterFailure,
    )

    assert result.outcome is ReservationConfirmationOutcome.INCONCLUSIVE
    assert result.observed_at == NOW


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (
            _AdapterFailure("provider_access_restricted", protection=True),
            ReservationConfirmationOutcome.PROVIDER_BLOCKED,
        ),
        (
            _AdapterFailure("rate_limited", rate_limited=True),
            ReservationConfirmationOutcome.PROVIDER_BLOCKED,
        ),
        (
            _AdapterFailure("source_unavailable"),
            ReservationConfirmationOutcome.INCONCLUSIVE,
        ),
    ],
)
async def test_owner_normalizes_transport_failures_without_retry(
    failure: _AdapterFailure,
    expected: ReservationConfirmationOutcome,
) -> None:
    calls = 0
    clock_calls = 0

    async def fail(
        _request: KorailReservationConfirmationRequest,
    ) -> KorailReservationConfirmationResult:
        nonlocal calls
        calls += 1
        raise failure

    def now() -> datetime:
        nonlocal clock_calls
        clock_calls += 1
        return NOW

    result = await owner.confirm_korail_sidecar_reservation(
        enabled=True,
        target=_target(),
        confirm=fail,
        normalize_train_number=lambda _value: "43",
        now=now,
        adapter_failure_type=_AdapterFailure,
    )

    assert calls == 1
    assert clock_calls == 1
    assert result.outcome is expected
    assert result.observed_at == NOW


async def test_owner_fails_closed_for_invalid_request_or_wire_result() -> None:
    calls = 0
    clock_calls = 0

    async def invalid_result(
        _request: KorailReservationConfirmationRequest,
    ) -> KorailReservationConfirmationResult:
        nonlocal calls
        calls += 1
        return KorailReservationConfirmationResult.model_construct(
            outcome="unexpected",
            source="private raw detail",
            observed_at=NOW,
        )

    def now() -> datetime:
        nonlocal clock_calls
        clock_calls += 1
        return NOW

    def invalid_normalizer(_value: object) -> str:
        raise ValueError("invalid train number")

    invalid_request = await owner.confirm_korail_sidecar_reservation(
        enabled=True,
        target=_target(),
        confirm=invalid_result,
        normalize_train_number=lambda _value: "",
        now=now,
        adapter_failure_type=_AdapterFailure,
    )
    invalid_normalizer_result = await owner.confirm_korail_sidecar_reservation(
        enabled=True,
        target=_target(),
        confirm=invalid_result,
        normalize_train_number=invalid_normalizer,
        now=now,
        adapter_failure_type=_AdapterFailure,
    )
    invalid_wire = await owner.confirm_korail_sidecar_reservation(
        enabled=True,
        target=_target(),
        confirm=invalid_result,
        normalize_train_number=lambda _value: "43",
        now=now,
        adapter_failure_type=_AdapterFailure,
    )

    assert calls == 1
    assert clock_calls == 3
    assert invalid_request.outcome is ReservationConfirmationOutcome.INCONCLUSIVE
    assert invalid_normalizer_result.outcome is ReservationConfirmationOutcome.INCONCLUSIVE
    assert invalid_wire.outcome is ReservationConfirmationOutcome.INCONCLUSIVE


@pytest.mark.parametrize(
    "target",
    [
        _target(),
        _target(purpose=ReservationConfirmationPurpose.PAYMENT_FOLLOW_UP),
        _target(
            purpose=ReservationConfirmationPurpose.PAYMENT_FOLLOW_UP,
            passenger_count=2,
            reserved_seats=(ReservationConfirmationSeat(car_number="4", seat_number="8A"),),
        ),
    ],
)
async def test_owner_rejects_wire_paid_for_ineligible_target_correlation(
    target: ReservationConfirmationTarget,
) -> None:
    async def confirm(
        _request: KorailReservationConfirmationRequest,
    ) -> KorailReservationConfirmationResult:
        return KorailReservationConfirmationResult(
            outcome="confirmed_paid",
            source="korail-issued-ticket-list",
            observed_at=NOW,
        )

    result = await owner.confirm_korail_sidecar_reservation(
        enabled=True,
        target=target,
        confirm=confirm,
        normalize_train_number=lambda _value: "43",
        now=lambda: NOW,
        adapter_failure_type=_AdapterFailure,
    )

    assert result.outcome is ReservationConfirmationOutcome.INCONCLUSIVE


async def test_owner_accepts_wire_paid_for_follow_up_with_one_persisted_seat() -> None:
    target = _target(
        purpose=ReservationConfirmationPurpose.PAYMENT_FOLLOW_UP,
        reserved_seats=(ReservationConfirmationSeat(car_number="4", seat_number="8A"),),
    )
    requests: list[KorailReservationConfirmationRequest] = []

    async def confirm(
        request: KorailReservationConfirmationRequest,
    ) -> KorailReservationConfirmationResult:
        requests.append(request)
        return KorailReservationConfirmationResult(
            outcome="confirmed_paid",
            source="korail-issued-ticket-list",
            observed_at=NOW,
        )

    result = await owner.confirm_korail_sidecar_reservation(
        enabled=True,
        target=target,
        confirm=confirm,
        normalize_train_number=lambda _value: "43",
        now=lambda: NOW,
        adapter_failure_type=_AdapterFailure,
    )

    assert result.outcome is ReservationConfirmationOutcome.CONFIRMED_PAID
    assert requests[0].purpose == "payment_follow_up"
    assert [seat.model_dump() for seat in requests[0].reserved_seats] == [
        {"car_number": "4", "seat_number": "8A"}
    ]


async def test_legacy_wrapper_keeps_pickle_late_runtime_and_dependency_seams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = ReservationConfirmationResult(
        provider=Provider.KORAIL,
        outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
        source="korail-same-session-detail",
        observed_at=NOW,
    )
    captured: dict[str, object] = {}

    class ReplacementFailure(_AdapterFailure):
        pass

    class ReplacementTransport:
        def __init__(self, name: str) -> None:
            self.name = name
            self.requests: list[KorailReservationConfirmationRequest] = []
            self.closed = False

        async def confirm_reservation(
            self,
            request: KorailReservationConfirmationRequest,
        ) -> KorailReservationConfirmationResult:
            self.requests.append(request)
            return KorailReservationConfirmationResult(
                outcome="inconclusive",
                source="korail-same-session-detail",
                observed_at=NOW,
            )

        async def close(self) -> None:
            self.closed = True

    initial_transport = ReplacementTransport("initial")
    replacement_transport = ReplacementTransport("replacement")
    source = legacy.KorailBrowserSeatSource(
        enabled=True,
        adapter_url="http://korail-browser-adapter:8001",
        cache_ttl_seconds=30,
        timeout_seconds=35,
        rate_limit_cooldown_seconds=300,
        protection_cooldown_seconds=300,
        transport=initial_transport,
    )

    async def fake_runtime(**kwargs: object) -> ReservationConfirmationResult:
        captured.update(kwargs)
        normalizer = kwargs["normalize_train_number"]
        confirm = kwargs["confirm"]
        target = _target()
        captured["wire_result"] = await confirm(
            KorailReservationConfirmationRequest(
                attempt_id=target.attempt_id,
                candidate_id=target.candidate_id,
                train_number=normalizer(target.train_number),
                origin=target.origin,
                destination=target.destination,
                departure_at=target.departure_at,
                arrival_at=target.arrival_at,
                seat_class="standard",
                passenger_count=1,
                credential_version=target.credential_version,
            )
        )
        return expected

    source._transport = replacement_transport
    monkeypatch.setattr(owner, "confirm_korail_sidecar_reservation", fake_runtime)
    monkeypatch.setattr(legacy, "_normalize_train_number", lambda _value: "99")
    monkeypatch.setattr(legacy, "_AdapterFailure", ReplacementFailure)
    result = await source.confirm_reservation(_target())
    await source.close()

    assert result is expected
    assert captured["enabled"] is True
    assert captured["target"] == _target()
    assert captured["adapter_failure_type"] is ReplacementFailure
    assert replacement_transport.requests[0].train_number == "99"
    assert replacement_transport.closed is True
    assert initial_transport.requests == []
    assert initial_transport.closed is False
    assert (
        pickle.loads(base64.b64decode(LEGACY_METHOD_PICKLE))
        is legacy.KorailBrowserSeatSource.confirm_reservation
    )


def test_runtime_owner_has_no_reverse_dependency_and_imports_without_source_reentry() -> None:
    owner_path = (
        API_ROOT
        / "src"
        / "rail_waitlist"
        / "reservations"
        / "provider_confirmation"
        / "korail_sidecar_runtime.py"
    )
    tree = ast.parse(owner_path.read_text(encoding="utf-8"), filename=str(owner_path))
    forbidden_roots = {
        "database",
        "fastapi",
        "korail_browser_seat_source",
        "main",
        "models",
        "services",
        "settings",
        "timetable_management",
        "worker",
    }
    imported_roots = {
        (node.module or "").split(".")[-1]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert imported_roots.isdisjoint(forbidden_roots)

    code = """
import json
import sys
from rail_waitlist.reservations.provider_confirmation import korail_sidecar_runtime as owner
print(json.dumps({
    "legacy_loaded": "rail_waitlist.korail_browser_seat_source" in sys.modules,
    "module": owner.confirm_korail_sidecar_reservation.__module__,
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == (
        '{"legacy_loaded": false, "module": '
        '"rail_waitlist.reservations.provider_confirmation.korail_sidecar_runtime"}'
    )
