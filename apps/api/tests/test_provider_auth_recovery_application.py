from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import rail_waitlist.services as services_module
from rail_waitlist.domain import Provider, ReservationOutcome, WatchStatus
from rail_waitlist.models import (
    ReservationAttempt,
    Watch,
    WatchCandidate,
    WatchTransitionHistory,
)
from rail_waitlist.provider_account_management.auth_recovery_application import (
    ProviderAuthRecoveryDependencies,
)
from rail_waitlist.provider_account_management.auth_recovery_application import (
    resume_watches_after_verified_provider_login as resume_watches_application,
)
from rail_waitlist.services import resume_watches_after_verified_provider_login


class ScalarRows:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def all(self) -> list[object]:
        return self.values


class ScriptedSession:
    def __init__(
        self,
        *,
        scalar_results: list[object | None],
        scalars_results: list[list[object]],
    ) -> None:
        self.scalar_results = scalar_results
        self.scalars_results = scalars_results
        self.scalar_statements: list[object] = []
        self.scalars_statements: list[object] = []

    async def scalar(self, statement: object) -> object | None:
        self.scalar_statements.append(statement)
        return self.scalar_results.pop(0)

    async def scalars(self, statement: object) -> ScalarRows:
        self.scalars_statements.append(statement)
        return ScalarRows(self.scalars_results.pop(0))


def make_watch(suffix: str = "default") -> Watch:
    return Watch(
        id=f"watch-{suffix}",
        provider=Provider.KORAIL,
        origin="대전",
        origin_node_id="NAT011668",
        destination="서울",
        destination_node_id="NAT010000",
        travel_date=date(2030, 8, 1),
        time_from=time(8),
        time_to=time(18),
        seat_class="standard",
        passenger_count=1,
        train_numbers=["00055"],
        notification_channel_ids=[],
        mode="official",
        status=WatchStatus.AUTH_REQUIRED,
        dedupe_key=f"auth-recovery-{suffix}",
    )


def make_candidate(
    watch: Watch,
    suffix: str = "default",
    *,
    state: str = "failed",
) -> WatchCandidate:
    return WatchCandidate(
        id=f"candidate-{suffix}",
        watch_id=watch.id,
        train_number="00055",
        departure_at=datetime(2030, 8, 1, 3, tzinfo=UTC),
        scheduled_departure_at=datetime(2030, 8, 1, 3, tzinfo=UTC),
        seat_class="standard",
        priority=1,
        state=state,
    )


def make_attempt(
    candidate: WatchCandidate,
    outcome: ReservationOutcome,
    suffix: str = "default",
) -> ReservationAttempt:
    started_at = datetime(2030, 7, 31, 23, tzinfo=UTC)
    return ReservationAttempt(
        id=f"attempt-{suffix}",
        candidate_id=candidate.id,
        attempt_sequence=1,
        episode_key=f"availability:{suffix}",
        idempotency_key=f"reserve:{suffix}",
        started_at=started_at,
        finished_at=started_at + timedelta(minutes=1),
        outcome=outcome,
    )


def make_transition(
    watch: Watch,
    reason: str,
    created_at: datetime,
) -> WatchTransitionHistory:
    return WatchTransitionHistory(
        id=f"transition-{watch.id}",
        watch_id=watch.id,
        from_status=WatchStatus.RESERVING,
        to_status=WatchStatus.AUTH_REQUIRED,
        reason=reason,
        created_at=created_at,
    )


@pytest.mark.parametrize(
    (
        "transition_reason",
        "attempt_outcome",
        "initial_candidate_state",
        "expected_candidate_state",
        "expected_resume_reason",
        "transition_after_authentication",
    ),
    [
        (
            "reservation_auth_required",
            ReservationOutcome.AUTH_REQUIRED,
            "failed",
            "observed",
            "provider_login_reverified",
            False,
        ),
        (
            "reservation_provider_blocked",
            ReservationOutcome.PROVIDER_BLOCKED,
            "failed",
            "observed",
            "provider_login_reverified_after_provider_block",
            False,
        ),
        (
            "reservation_unknown",
            ReservationOutcome.UNKNOWN,
            "failed",
            "observed",
            "reservation_unknown_monitoring_resumed",
            True,
        ),
        (
            "provider_account_not_authenticated_before_reservation",
            None,
            "seat_found",
            "seat_found",
            "provider_login_reverified_before_reservation",
            False,
        ),
    ],
)
async def test_auth_recovery_owner_preserves_reason_time_and_candidate_contract(
    transition_reason: str,
    attempt_outcome: ReservationOutcome | None,
    initial_candidate_state: str,
    expected_candidate_state: str,
    expected_resume_reason: str,
    transition_after_authentication: bool,
) -> None:
    authenticated_at = datetime(2030, 8, 1, 1, tzinfo=UTC).replace(tzinfo=None)
    transition_at = authenticated_at + (
        timedelta(minutes=1) if transition_after_authentication else -timedelta(minutes=1)
    )
    watch = make_watch(transition_reason)
    candidate = make_candidate(
        watch,
        transition_reason,
        state=initial_candidate_state,
    )
    scalar_results: list[object | None] = [
        watch,
        make_transition(watch, transition_reason, transition_at),
    ]
    if attempt_outcome is not None:
        scalar_results.append(make_attempt(candidate, attempt_outcome, transition_reason))
    session = ScriptedSession(
        scalar_results=scalar_results,
        scalars_results=[[watch.id], [candidate]],
    )
    transitions: list[tuple[WatchStatus, str | None]] = []

    async def apply_transition(
        _session: AsyncSession,
        transitioned_watch: Watch,
        target: WatchStatus,
        idempotency_key: str | None = None,
        *,
        reason: str | None = None,
    ) -> Watch:
        assert idempotency_key is None
        transitions.append((target, reason))
        transitioned_watch.status = target
        return transitioned_watch

    resumed = await resume_watches_application(
        cast(AsyncSession, session),
        Provider.KORAIL,
        authenticated_at,
        dependencies=ProviderAuthRecoveryDependencies(
            apply_watch_transition=apply_transition,
        ),
    )

    assert resumed == [watch.id]
    assert watch.status is WatchStatus.SCHEDULED
    assert candidate.state == expected_candidate_state
    assert transitions == [(WatchStatus.SCHEDULED, expected_resume_reason)]
    assert session.scalar_results == []
    assert session.scalars_results == []


@pytest.mark.parametrize(
    ("locked_watch", "reason", "transition_offset", "has_latest_transition"),
    [
        (False, "reservation_auth_required", -1, True),
        (True, "reservation_auth_required", -1, False),
        (True, "unrelated_reason", -1, True),
        (True, "reservation_auth_required", 1, True),
        (True, "provider_account_not_authenticated_before_reservation", 1, True),
    ],
)
async def test_auth_recovery_owner_skips_stale_or_unrelated_watch_state(
    locked_watch: bool,
    reason: str,
    transition_offset: int,
    has_latest_transition: bool,
) -> None:
    authenticated_at = datetime(2030, 8, 1, 1, tzinfo=UTC)
    watch = make_watch(f"skip-{reason}-{transition_offset}-{has_latest_transition}")
    scalar_results: list[object | None] = [watch if locked_watch else None]
    if locked_watch:
        scalar_results.append(
            make_transition(
                watch,
                reason,
                authenticated_at + timedelta(minutes=transition_offset),
            )
            if has_latest_transition
            else None
        )
    session = ScriptedSession(
        scalar_results=scalar_results,
        scalars_results=[[watch.id]],
    )

    async def fail_transition(*_args: object, **_kwargs: object) -> Watch:
        raise AssertionError("ineligible auth recovery must not transition")

    resumed = await resume_watches_application(
        cast(AsyncSession, session),
        Provider.KORAIL,
        authenticated_at,
        dependencies=ProviderAuthRecoveryDependencies(
            apply_watch_transition=fail_transition,
        ),
    )

    assert resumed == []
    assert session.scalar_results == []


async def test_auth_recovery_owner_changes_only_matching_failed_candidates() -> None:
    authenticated_at = datetime(2030, 8, 1, 1, tzinfo=UTC)
    watch = make_watch("candidate-matrix")
    matching = make_candidate(watch, "matching")
    mismatched = make_candidate(watch, "mismatched")
    active = make_candidate(watch, "active", state="observed")
    session = ScriptedSession(
        scalar_results=[
            watch,
            make_transition(
                watch,
                "reservation_auth_required",
                authenticated_at - timedelta(minutes=1),
            ),
            make_attempt(matching, ReservationOutcome.AUTH_REQUIRED, "matching"),
            make_attempt(mismatched, ReservationOutcome.FAILED, "mismatched"),
        ],
        scalars_results=[[watch.id], [matching, mismatched, active]],
    )
    transitions: list[str | None] = []

    async def apply_transition(
        _session: AsyncSession,
        transitioned_watch: Watch,
        target: WatchStatus,
        idempotency_key: str | None = None,
        *,
        reason: str | None = None,
    ) -> Watch:
        del idempotency_key
        transitions.append(reason)
        transitioned_watch.status = target
        return transitioned_watch

    resumed = await resume_watches_application(
        cast(AsyncSession, session),
        Provider.KORAIL,
        authenticated_at,
        dependencies=ProviderAuthRecoveryDependencies(
            apply_watch_transition=apply_transition,
        ),
    )

    assert resumed == [watch.id]
    assert matching.state == "observed"
    assert mismatched.state == "failed"
    assert active.state == "observed"
    assert transitions == ["provider_login_reverified"]


async def test_services_auth_recovery_wrapper_resolves_current_transition_global(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[ProviderAuthRecoveryDependencies] = []
    transition_sentinel = cast(object, object())

    async def application(*_args: object, **kwargs: object) -> list[str]:
        captured.append(cast(ProviderAuthRecoveryDependencies, kwargs["dependencies"]))
        return ["watch-returned"]

    monkeypatch.setattr(
        services_module,
        "resume_provider_login_watches_application",
        application,
    )
    monkeypatch.setattr(services_module, "apply_watch_transition", transition_sentinel)

    returned = await resume_watches_after_verified_provider_login(
        cast(AsyncSession, object()),
        Provider.KORAIL,
        datetime(2030, 8, 1, tzinfo=UTC),
    )

    assert returned == ["watch-returned"]
    assert resume_watches_after_verified_provider_login is (
        services_module.resume_watches_after_verified_provider_login
    )
    assert resume_watches_after_verified_provider_login.__module__ == "rail_waitlist.services"
    assert captured[0].apply_watch_transition is transition_sentinel


async def test_services_auth_recovery_wrapper_does_not_translate_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail(*_args: object, **_kwargs: object) -> list[str]:
        raise RuntimeError("recovery transaction failed")

    monkeypatch.setattr(
        services_module,
        "resume_provider_login_watches_application",
        fail,
    )

    with pytest.raises(RuntimeError, match="recovery transaction failed"):
        await resume_watches_after_verified_provider_login(
            cast(AsyncSession, object()),
            Provider.KORAIL,
            datetime(2030, 8, 1, tzinfo=UTC),
        )
