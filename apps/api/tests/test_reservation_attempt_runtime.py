from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import rail_waitlist.reservations.attempt_runtime as runtime_module
from rail_waitlist.domain import ReservationOutcome, SeatObservationStatus
from rail_waitlist.reservations.attempt_claim_application import (
    ReservationAttemptClaimDependencies,
)
from rail_waitlist.reservations.attempt_result_application import (
    ReservationAttemptAlreadyCompleted,
    ReservationAttemptResultDependencies,
)
from rail_waitlist.reservations.contracts import ReservationResult
from rail_waitlist.reservations.domain import (
    ReservationAttemptResultPolicy,
)
from rail_waitlist.reservations.provider_confirmation.contracts import (
    ReservationConfirmationResult,
)
from rail_waitlist.watch_management.models import ReservationAttempt, Watch, WatchCandidate

OBSERVED_AT = datetime(2026, 8, 7, 3, 4, tzinfo=UTC)


def test_claim_dependency_factory_reads_replaceable_runtime_globals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def transition(*_args: object, **_kwargs: object) -> Watch:
        raise AssertionError("dependency assembly must not invoke transitions")

    async def outbox(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("dependency assembly must not emit outbox events")

    def payment_hold(_attempt: ReservationAttempt) -> bool:
        return False

    def confirmed_absent(_attempt: ReservationAttempt) -> bool:
        return False

    actionable_statuses = frozenset({SeatObservationStatus.AVAILABLE})
    monkeypatch.setattr(runtime_module, "apply_watch_transition", transition)
    monkeypatch.setattr(runtime_module, "add_outbox_event", outbox)
    monkeypatch.setattr(runtime_module, "is_payment_hold_ended", payment_hold)
    monkeypatch.setattr(runtime_module, "is_confirmed_absent_retry_source", confirmed_absent)
    monkeypatch.setattr(runtime_module, "ACTIONABLE_SEAT_STATUSES", actionable_statuses)

    dependencies = runtime_module.reservation_attempt_claim_dependencies()

    assert isinstance(dependencies, ReservationAttemptClaimDependencies)
    assert dependencies.apply_watch_transition is transition
    assert dependencies.add_outbox_event is outbox
    assert dependencies.is_payment_hold_ended is payment_hold
    assert dependencies.is_confirmed_absent_retry_source is confirmed_absent
    assert dependencies.actionable_seat_statuses is actionable_statuses


def test_result_dependency_factory_reads_replaceable_runtime_globals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def transition(*_args: object, **_kwargs: object) -> Watch:
        raise AssertionError("dependency assembly must not invoke transitions")

    async def outbox(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("dependency assembly must not emit outbox events")

    def policy(_outcome: ReservationOutcome) -> ReservationAttemptResultPolicy:
        return ReservationAttemptResultPolicy(
            retryable=False,
            manual_check_required=True,
            retry_condition=None,
        )

    def confirmation(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("dependency assembly must not record confirmation")

    class FrozenDateTime:
        @staticmethod
        def now(timezone: object) -> datetime:
            assert timezone is UTC
            return OBSERVED_AT

    monkeypatch.setattr(runtime_module, "apply_watch_transition", transition)
    monkeypatch.setattr(runtime_module, "add_outbox_event", outbox)
    monkeypatch.setattr(runtime_module, "reservation_attempt_result_policy", policy)
    monkeypatch.setattr(runtime_module, "record_reservation_confirmation", confirmation)
    monkeypatch.setattr(runtime_module, "datetime", FrozenDateTime)

    dependencies = runtime_module.reservation_attempt_result_dependencies()

    assert isinstance(dependencies, ReservationAttemptResultDependencies)
    assert dependencies.apply_watch_transition is transition
    assert dependencies.add_outbox_event is outbox
    assert dependencies.result_policy is policy
    assert dependencies.record_reservation_confirmation is confirmation
    assert dependencies.now() is OBSERVED_AT


async def test_begin_runtime_preserves_arguments_and_uses_current_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = cast(AsyncSession, object())
    watch = cast(Watch, object())
    candidate = cast(WatchCandidate, object())
    expected_attempt = cast(ReservationAttempt, object())
    expected_dependencies = cast(ReservationAttemptClaimDependencies, object())
    captured: dict[str, object] = {}

    def dependencies_factory() -> ReservationAttemptClaimDependencies:
        return expected_dependencies

    async def application(*args: object, **kwargs: object) -> tuple[ReservationAttempt, bool]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return expected_attempt, True

    monkeypatch.setattr(
        runtime_module,
        "reservation_attempt_claim_dependencies",
        dependencies_factory,
    )
    monkeypatch.setattr(runtime_module, "begin_reservation_attempt_application", application)

    result = await runtime_module.begin_reservation_attempt(
        session,
        watch,
        candidate,
        "attempt-key",
        episode_key="availability:episode",
        retry_authorized=True,
        credential_version=7,
    )

    assert result == (expected_attempt, True)
    assert captured == {
        "args": (session, watch, candidate, "attempt-key"),
        "kwargs": {
            "episode_key": "availability:episode",
            "retry_authorized": True,
            "credential_version": 7,
            "dependencies": expected_dependencies,
        },
    }


async def test_complete_runtime_preserves_arguments_and_uses_current_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = cast(AsyncSession, object())
    watch = cast(Watch, object())
    candidate = cast(WatchCandidate, object())
    attempt = cast(ReservationAttempt, object())
    confirmation = cast(ReservationConfirmationResult, object())
    result = ReservationResult(
        outcome=ReservationOutcome.UNKNOWN,
        source="runtime.owner-test",
        observed_at=OBSERVED_AT,
    )
    expected_dependencies = cast(ReservationAttemptResultDependencies, object())
    captured: dict[str, object] = {}

    def dependencies_factory() -> ReservationAttemptResultDependencies:
        return expected_dependencies

    async def application(*args: object, **kwargs: object) -> None:
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(
        runtime_module,
        "reservation_attempt_result_dependencies",
        dependencies_factory,
    )
    monkeypatch.setattr(runtime_module, "complete_reservation_attempt_application", application)

    await runtime_module.complete_reservation_attempt(
        session,
        watch,
        candidate,
        attempt,
        result,
        confirmation,
    )

    assert captured == {
        "args": (session, watch, candidate, attempt, result, confirmation),
        "kwargs": {"dependencies": expected_dependencies},
    }


async def test_complete_runtime_propagates_domain_conflict_without_http_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conflict = ReservationAttemptAlreadyCompleted("already completed")

    async def application(*_args: object, **_kwargs: object) -> None:
        raise conflict

    monkeypatch.setattr(runtime_module, "complete_reservation_attempt_application", application)

    with pytest.raises(ReservationAttemptAlreadyCompleted) as raised:
        await runtime_module.complete_reservation_attempt(
            cast(AsyncSession, object()),
            cast(Watch, object()),
            cast(WatchCandidate, object()),
            cast(ReservationAttempt, object()),
            ReservationResult(
                outcome=ReservationOutcome.UNKNOWN,
                source="runtime.owner-test",
                observed_at=OBSERVED_AT,
            ),
        )

    assert raised.value is conflict


async def test_runtime_does_not_end_or_refresh_the_caller_owned_unit_of_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class GuardSession:
        def __getattribute__(self, name: str) -> object:
            if name in {"commit", "rollback", "refresh"}:
                raise AssertionError(f"runtime must not access session.{name}")
            return super().__getattribute__(name)

    async def begin_application(*_args: object, **_kwargs: object):
        return cast(ReservationAttempt, object()), True

    async def complete_application(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        runtime_module,
        "begin_reservation_attempt_application",
        begin_application,
    )
    monkeypatch.setattr(
        runtime_module,
        "complete_reservation_attempt_application",
        complete_application,
    )

    session = cast(AsyncSession, GuardSession())
    watch = cast(Watch, object())
    candidate = cast(WatchCandidate, object())
    attempt, created = await runtime_module.begin_reservation_attempt(
        session,
        watch,
        candidate,
        "attempt-key",
    )
    await runtime_module.complete_reservation_attempt(
        session,
        watch,
        candidate,
        attempt,
        ReservationResult(
            outcome=ReservationOutcome.UNKNOWN,
            source="runtime.owner-test",
            observed_at=OBSERVED_AT,
        ),
    )

    assert created is True
