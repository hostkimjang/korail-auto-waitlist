from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import rail_waitlist.services as services_module
import rail_waitlist.worker as worker_module
from rail_waitlist.domain import Provider, SeatClass, SeatObservationStatus, WatchStatus
from rail_waitlist.models import SeatObservation, Watch, WatchCandidate
from rail_waitlist.observations.operational_projection_application import (
    OperationalProjectionCandidate,
    apply_operational_projection,
)
from rail_waitlist.observations.recording_application import (
    ObservationRecordingDependencies,
)
from rail_waitlist.observations.recording_application import (
    record_seat_observation as record_seat_observation_application,
)
from rail_waitlist.schemas import SeatObservationResult
from rail_waitlist.services import record_seat_observation


class RecordingSession:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.observation: SeatObservation | None = None

    def add(self, value: object) -> None:
        assert isinstance(value, SeatObservation)
        self.observation = value
        self.events.append("add")

    async def flush(self) -> None:
        assert self.observation is not None
        self.observation.id = "observation-recording"
        self.events.append("flush")


def make_watch_and_candidate() -> tuple[Watch, WatchCandidate]:
    departure_at = datetime(2030, 8, 1, 3, tzinfo=UTC)
    watch = Watch(
        id="watch-recording",
        provider=Provider.MOCK,
        origin="서울",
        origin_node_id="N-SEOUL",
        destination="부산",
        destination_node_id="N-BUSAN",
        travel_date=date(2030, 8, 1),
        time_from=time(12),
        time_to=time(18),
        seat_class="standard",
        passenger_count=1,
        train_numbers=["MOCK-001"],
        notification_channel_ids=[],
        mode="official",
        status=WatchStatus.WATCHING,
        dedupe_key="watch-recording",
    )
    candidate = WatchCandidate(
        id="candidate-recording",
        watch_id=watch.id,
        train_number="MOCK-001",
        departure_at=departure_at,
        scheduled_departure_at=departure_at,
        seat_class="standard",
        priority=1,
        state="active",
    )
    return watch, candidate


def result(status: SeatObservationStatus) -> SeatObservationResult:
    observed_at = datetime(2030, 8, 1, tzinfo=UTC)
    return SeatObservationResult(
        seat_class=SeatClass.STANDARD,
        status=status,
        source="recording-test",
        observed_at=observed_at,
        fresh_until=observed_at + timedelta(minutes=5),
    )


@pytest.mark.parametrize(
    ("status", "candidate_state", "target", "reason"),
    [
        (
            SeatObservationStatus.AVAILABLE,
            "seat_found",
            WatchStatus.SEAT_FOUND,
            "authorized_seat_observation_actionable",
        ),
        (
            SeatObservationStatus.WAITLIST_AVAILABLE,
            "seat_found",
            WatchStatus.OFFICIAL_WAITLIST,
            "authorized_seat_observation_waitlist_available",
        ),
        (SeatObservationStatus.SOLD_OUT, "observed", None, None),
    ],
)
async def test_recording_owner_preserves_projection_outbox_and_transition_order(
    status: SeatObservationStatus,
    candidate_state: str,
    target: WatchStatus | None,
    reason: str | None,
) -> None:
    events: list[str] = []
    transitions: list[tuple[WatchStatus, str | None, SeatObservation | None]] = []
    outbox_payloads: list[dict[str, object]] = []
    watch, candidate = make_watch_and_candidate()

    def project(
        candidate: OperationalProjectionCandidate,
        observation_result: SeatObservationResult,
    ) -> None:
        events.append("project")
        apply_operational_projection(candidate, observation_result)

    async def add_outbox_event(
        _session: AsyncSession,
        *,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, object],
        dedupe_key: str,
    ) -> object:
        events.append("outbox")
        assert (aggregate_type, aggregate_id, event_type) == (
            "watch",
            watch.id,
            "watch.seat_observed",
        )
        assert dedupe_key == "seat-observation:observation-recording"
        outbox_payloads.append(payload)
        return object()

    async def transition(
        _session: AsyncSession,
        transitioned_watch: Watch,
        transition_target: WatchStatus,
        idempotency_key: str | None = None,
        *,
        reason: str | None = None,
        observation: SeatObservation | None = None,
    ) -> Watch:
        del idempotency_key
        events.append("transition")
        transitions.append((transition_target, reason, observation))
        return transitioned_watch

    observation = await record_seat_observation_application(
        cast(AsyncSession, RecordingSession(events)),
        watch,
        candidate,
        result(status),
        dependencies=ObservationRecordingDependencies(
            apply_operational_projection=project,
            add_outbox_event=add_outbox_event,
            apply_watch_transition=transition,
        ),
    )

    assert events == ["project", "add", "flush", "outbox"] + (
        ["transition"] if target is not None else []
    )
    assert candidate.state == candidate_state
    assert outbox_payloads == [
        {
            "watch_id": watch.id,
            "candidate_id": candidate.id,
            "status": status.value,
            "source": "recording-test",
            "observed_at": "2030-08-01T00:00:00+00:00",
            "fresh_until": "2030-08-01T00:05:00+00:00",
        }
    ]
    assert transitions == ([(target, reason, observation)] if target is not None else [])


async def test_recording_owner_can_defer_watch_summary_transition() -> None:
    events: list[str] = []
    watch, candidate = make_watch_and_candidate()

    async def add_outbox_event(*_args: object, **_kwargs: object) -> object:
        events.append("outbox")
        return object()

    async def fail_transition(*_args: object, **_kwargs: object) -> Watch:
        raise AssertionError("summary transition belongs to the observation group")

    await record_seat_observation_application(
        cast(AsyncSession, RecordingSession(events)),
        watch,
        candidate,
        result(SeatObservationStatus.AVAILABLE),
        apply_status_transition=False,
        dependencies=ObservationRecordingDependencies(
            apply_operational_projection=apply_operational_projection,
            add_outbox_event=add_outbox_event,
            apply_watch_transition=fail_transition,
        ),
    )

    assert candidate.state == "seat_found"
    assert events == ["add", "flush", "outbox"]


async def test_services_wrapper_assembles_dependencies_from_current_globals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[ObservationRecordingDependencies] = []
    sentinel = cast(SeatObservation, object())
    watch, candidate = make_watch_and_candidate()

    def project(*_args: object) -> None:
        return None

    async def add_outbox(*_args: object, **_kwargs: object) -> object:
        return object()

    async def transition(*_args: object, **_kwargs: object) -> Watch:
        return watch

    async def application(*_args: object, **kwargs: object) -> SeatObservation:
        captured.append(cast(ObservationRecordingDependencies, kwargs["dependencies"]))
        return sentinel

    monkeypatch.setattr(services_module, "apply_operational_projection", project)
    monkeypatch.setattr(services_module, "add_outbox_event", add_outbox)
    monkeypatch.setattr(services_module, "apply_watch_transition", transition)
    monkeypatch.setattr(services_module, "record_seat_observation_application", application)

    returned = await record_seat_observation(
        cast(AsyncSession, object()),
        watch,
        candidate,
        result(SeatObservationStatus.SOLD_OUT),
    )

    assert returned is sentinel
    assert record_seat_observation is services_module.record_seat_observation
    assert record_seat_observation.__module__ == "rail_waitlist.services"
    assert captured == [
        ObservationRecordingDependencies(
            apply_operational_projection=project,
            add_outbox_event=add_outbox,
            apply_watch_transition=transition,
        )
    ]


async def test_worker_dependency_uses_the_canonical_recording_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[ObservationRecordingDependencies] = []
    sentinel = cast(SeatObservation, object())
    watch, candidate = make_watch_and_candidate()

    async def application(*_args: object, **kwargs: object) -> SeatObservation:
        captured.append(cast(ObservationRecordingDependencies, kwargs["dependencies"]))
        return sentinel

    monkeypatch.setattr(worker_module, "record_seat_observation_application", application)
    dependencies = worker_module._observation_group_dependencies()

    returned = await dependencies.record_seat_observation(
        cast(AsyncSession, object()),
        watch,
        candidate,
        result(SeatObservationStatus.SOLD_OUT),
    )

    assert returned is sentinel
    assert captured == [
        ObservationRecordingDependencies(
            apply_operational_projection=worker_module.apply_operational_projection,
            add_outbox_event=worker_module.add_outbox_event,
            apply_watch_transition=worker_module.apply_watch_transition,
        )
    ]
