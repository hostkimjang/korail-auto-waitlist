from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time
from typing import cast

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import rail_waitlist.services as services_module
from rail_waitlist.domain import Provider, SeatObservationMode, WatchStatus
from rail_waitlist.models import Watch
from rail_waitlist.schemas import WatchCreate
from rail_waitlist.services import create_watch
from rail_waitlist.watch_management.create_application import (
    WatchCreateDependencies,
    WatchCreateForbidden,
    WatchCreateValidationError,
    WatchRegistrationEvidenceExpired,
)
from rail_waitlist.watch_management.create_application import (
    create_watch as create_watch_application,
)


class ScalarRows:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def all(self) -> list[object]:
        return self.values


class RecordingSession:
    def __init__(
        self,
        events: list[str],
        *,
        existing: dict[str, Watch] | None = None,
        scalar_values: list[object] | None = None,
        commit_error: IntegrityError | None = None,
    ) -> None:
        self.events = events
        self.existing = existing or {}
        self.scalar_values = scalar_values or []
        self.commit_error = commit_error
        self.added: Watch | None = None

    async def get(self, _model: type[Watch], resource_id: str) -> Watch | None:
        self.events.append(f"get:{resource_id}")
        return self.existing.get(resource_id)

    async def scalars(self, _statement: object) -> ScalarRows:
        self.events.append("evidence-query")
        return ScalarRows(self.scalar_values)

    def add(self, value: object) -> None:
        assert isinstance(value, Watch)
        self.added = value
        self.events.append("add")

    async def flush(self) -> None:
        assert self.added is not None
        self.added.id = "watch-created"
        self.events.append("flush")

    async def commit(self) -> None:
        self.events.append("commit")
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self) -> None:
        self.events.append("rollback")

    async def refresh(self, value: object) -> None:
        assert value is self.added
        self.events.append("refresh")


def make_data(
    *,
    provider: Provider = Provider.MOCK,
    mode: str = "official",
    registration_evidence_id: str | None = None,
    seat_observation_mode: SeatObservationMode = SeatObservationMode.BALANCED,
) -> WatchCreate:
    train_number = "26" if provider in {Provider.KORAIL, Provider.SRT} else "MOCK-001"
    return WatchCreate(
        provider=provider,
        origin="서울",
        origin_node_id="0010" if provider is not Provider.MOCK else "N-SEOUL",
        destination="부산",
        destination_node_id="0020" if provider is not Provider.MOCK else "N-BUSAN",
        travel_date=date(2030, 8, 1),
        time_from=time(8),
        time_to=time(18),
        seat_class="standard",
        passenger_count=1,
        train_numbers=[train_number],
        candidates=[
            {
                "train_number": train_number,
                "departure_at": datetime(2030, 8, 1, 3, tzinfo=UTC),
                "arrival_at": datetime(2030, 8, 1, 4, tzinfo=UTC),
                "seat_class": "standard",
                "priority": 1,
                "registration_evidence_id": registration_evidence_id,
            }
        ],
        notification_channel_ids=["channel-1"],
        mode=mode,
        seat_observation_mode=seat_observation_mode,
    )


def make_dependencies(
    events: list[str],
    *,
    idempotency_results: list[str | None] | None = None,
    experimental_enabled: bool = True,
    outbox_error: IntegrityError | None = None,
) -> WatchCreateDependencies:
    results = list(idempotency_results or [None])

    def request_hash(_value: object) -> str:
        events.append("hash")
        return "payload-hash"

    async def get_idempotent_resource(
        _session: AsyncSession,
        scope: str,
        key: str | None,
        payload_hash: str,
    ) -> str | None:
        assert (scope, payload_hash) == ("watch.create", "payload-hash")
        events.append(f"idempotency-get:{key}")
        return results.pop(0) if results else None

    async def ensure_capacity(
        _session: AsyncSession,
        provider: Provider,
        *,
        exclude_watch_id: str | None = None,
    ) -> None:
        assert exclude_watch_id is None
        events.append(f"capacity:{provider.value}")

    def experimental_rail_enabled() -> bool:
        events.append("experimental-enabled")
        return experimental_enabled

    async def validate_channels(_session: AsyncSession, channel_ids: list[str]) -> None:
        assert channel_ids == ["channel-1"]
        events.append("channels")

    def dedupe(*_args: object) -> str:
        events.append("dedupe")
        return "watch-dedupe"

    def booking_url(provider: Provider) -> str:
        events.append(f"booking-url:{provider.value}")
        return "https://example.test/booking"

    async def remember(
        _session: AsyncSession,
        scope: str,
        key: str | None,
        resource_id: str,
        payload_hash: str,
    ) -> None:
        assert (scope, resource_id, payload_hash) == (
            "watch.create",
            "watch-created",
            "payload-hash",
        )
        events.append(f"remember:{key}")

    async def outbox(
        _session: AsyncSession,
        *,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, object],
        dedupe_key: str,
    ) -> object:
        assert (aggregate_type, aggregate_id, event_type) == (
            "watch",
            "watch-created",
            "watch.created",
        )
        assert payload == {"watch_id": "watch-created", "status": "draft"}
        assert dedupe_key == "watch:watch-created:created"
        events.append("outbox")
        if outbox_error is not None:
            raise outbox_error
        return object()

    def now() -> datetime:
        events.append("now")
        return datetime(2030, 8, 1, 2, tzinfo=UTC)

    return WatchCreateDependencies(
        request_hash=request_hash,
        get_idempotent_resource=get_idempotent_resource,
        ensure_focused_observation_capacity=ensure_capacity,
        experimental_rail_enabled=experimental_rail_enabled,
        validate_channel_ids=validate_channels,
        build_watch_dedupe_key=dedupe,
        official_booking_url_for_provider=booking_url,
        remember_idempotency=remember,
        add_outbox_event=outbox,
        now=now,
    )


async def test_create_owner_preserves_order_persisted_shape_and_outbox_identity() -> None:
    events: list[str] = []
    session = RecordingSession(events)

    created = await create_watch_application(
        cast(AsyncSession, session),
        make_data(),
        "create-key",
        dependencies=make_dependencies(events),
    )

    assert created is session.added
    assert created.status is WatchStatus.DRAFT
    assert created.dedupe_key == "watch-dedupe"
    assert created.official_booking_url == "https://example.test/booking"
    assert len(created.candidates) == 1
    candidate = created.candidates[0]
    assert candidate.departure_at == datetime(2030, 8, 1, 3, tzinfo=UTC)
    assert candidate.scheduled_departure_at == candidate.departure_at
    assert candidate.arrival_at == datetime(2030, 8, 1, 4, tzinfo=UTC)
    assert events == [
        "hash",
        "idempotency-get:create-key",
        "channels",
        "dedupe",
        "booking-url:mock",
        "add",
        "flush",
        "remember:create-key",
        "outbox",
        "commit",
        "refresh",
    ]


async def test_idempotent_replay_short_circuits_all_later_dependencies() -> None:
    events: list[str] = []
    existing = Watch(
        id="existing-watch",
        provider=Provider.MOCK,
        origin="서울",
        destination="부산",
        travel_date=date(2030, 8, 1),
        time_from=time(8),
        time_to=time(18),
        seat_class="standard",
        passenger_count=1,
        train_numbers=["MOCK-001"],
        notification_channel_ids=[],
        mode="official",
        status=WatchStatus.DRAFT,
        dedupe_key="existing-dedupe",
    )
    session = RecordingSession(events, existing={existing.id: existing})

    replayed = await create_watch_application(
        cast(AsyncSession, session),
        make_data(seat_observation_mode=SeatObservationMode.FOCUSED),
        "replay-key",
        dependencies=make_dependencies(events, idempotency_results=[existing.id]),
    )

    assert replayed is existing
    assert session.added is None
    assert events == ["hash", "idempotency-get:replay-key", "get:existing-watch"]


@pytest.mark.parametrize("failure_stage", ["outbox", "commit"])
async def test_integrity_conflict_rolls_back_then_returns_idempotent_winner(
    failure_stage: str,
) -> None:
    events: list[str] = []
    error = IntegrityError(failure_stage, {}, RuntimeError("duplicate"))
    winner = Watch(
        id="winner-watch",
        provider=Provider.MOCK,
        origin="서울",
        destination="부산",
        travel_date=date(2030, 8, 1),
        time_from=time(8),
        time_to=time(18),
        status=WatchStatus.DRAFT,
        dedupe_key="winner-dedupe",
    )
    session = RecordingSession(
        events,
        existing={winner.id: winner},
        commit_error=error if failure_stage == "commit" else None,
    )

    returned = await create_watch_application(
        cast(AsyncSession, session),
        make_data(),
        "race-key",
        dependencies=make_dependencies(
            events,
            idempotency_results=[None, winner.id],
            outbox_error=error if failure_stage == "outbox" else None,
        ),
    )

    assert returned is winner
    assert events[-3:] == [
        "rollback",
        "idempotency-get:race-key",
        "get:winner-watch",
    ]
    assert ("commit" in events) is (failure_stage == "commit")
    assert "refresh" not in events


@pytest.mark.parametrize("idempotency_key", [None, "missing-winner"])
async def test_integrity_conflict_without_replay_winner_reraises_original_error(
    idempotency_key: str | None,
) -> None:
    events: list[str] = []
    error = IntegrityError("commit", {}, RuntimeError("duplicate"))
    session = RecordingSession(events, commit_error=error)

    with pytest.raises(IntegrityError) as raised:
        await create_watch_application(
            cast(AsyncSession, session),
            make_data(),
            idempotency_key,
            dependencies=make_dependencies(
                events,
                idempotency_results=[None, None],
            ),
        )

    assert raised.value is error
    assert events.count("rollback") == 1
    assert "refresh" not in events
    expected_gets = 1 if idempotency_key is None else 2
    assert len([event for event in events if event.startswith("idempotency-get:")]) == expected_gets


@pytest.mark.parametrize(
    ("data_factory", "experimental_enabled", "error_type", "message"),
    [
        (
            lambda: make_data(provider=Provider.SRT, mode="experimental"),
            False,
            WatchCreateForbidden,
            "experimental rail mode is disabled",
        ),
        (
            lambda: make_data(provider=Provider.KORAIL),
            True,
            WatchCreateValidationError,
            "official watch candidates require registration evidence",
        ),
        (
            lambda: make_data(registration_evidence_id="evidence-1"),
            True,
            WatchCreateValidationError,
            "registration evidence is only valid for official watches",
        ),
    ],
)
async def test_create_owner_preserves_fail_closed_application_errors(
    data_factory: Callable[[], WatchCreate],
    experimental_enabled: bool,
    error_type: type[RuntimeError],
    message: str,
) -> None:
    events: list[str] = []

    with pytest.raises(error_type, match=message):
        await create_watch_application(
            cast(AsyncSession, RecordingSession(events)),
            data_factory(),
            dependencies=make_dependencies(
                events,
                experimental_enabled=experimental_enabled,
            ),
        )


async def test_services_create_wrapper_resolves_current_globals_and_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[WatchCreateDependencies] = []
    sentinel = cast(Watch, object())

    async def application(*_args: object, **kwargs: object) -> Watch:
        captured.append(cast(WatchCreateDependencies, kwargs["dependencies"]))
        return sentinel

    class TimetableProvider:
        @staticmethod
        def official_booking_url() -> str:
            return "https://current-provider.test/booking"

    monkeypatch.setattr(services_module, "create_watch_application", application)
    monkeypatch.setattr(
        services_module,
        "get_timetable_provider",
        lambda _provider: TimetableProvider(),
    )

    returned = await create_watch(
        cast(AsyncSession, object()),
        make_data(),
        "wrapper-key",
    )

    assert returned is sentinel
    assert create_watch is services_module.create_watch
    assert create_watch.__module__ == "rail_waitlist.services"
    assert create_watch_application.__module__.endswith("create_application")
    dependencies = captured[0]
    assert dependencies.request_hash is services_module.request_hash
    assert dependencies.get_idempotent_resource is services_module.get_idempotent_resource
    assert (
        dependencies.ensure_focused_observation_capacity
        is services_module._ensure_focused_observation_capacity
    )
    assert dependencies.experimental_rail_enabled is services_module._experimental_rail_enabled
    assert dependencies.validate_channel_ids is services_module.validate_channel_ids
    assert dependencies.build_watch_dedupe_key is services_module.build_watch_dedupe_key
    assert dependencies.remember_idempotency is services_module.remember_idempotency
    assert dependencies.add_outbox_event is services_module.add_outbox_event
    assert dependencies.official_booking_url_for_provider(Provider.MOCK) == (
        "https://current-provider.test/booking"
    )
    assert dependencies.now().tzinfo is UTC


@pytest.mark.parametrize(
    ("error", "status_code", "detail"),
    [
        (WatchCreateForbidden("disabled"), 403, "disabled"),
        (WatchCreateValidationError("invalid"), 422, "invalid"),
        (
            WatchRegistrationEvidenceExpired(
                "좌석 등록 근거가 만료되었습니다. 좌석 상태를 다시 조회해 주세요."
            ),
            409,
            {
                "code": "registration_evidence_conflict",
                "reason": "expired",
                "message": "좌석 등록 근거가 만료되었습니다. 좌석 상태를 다시 조회해 주세요.",
            },
        ),
    ],
)
async def test_services_create_wrapper_preserves_http_error_contract(
    monkeypatch: pytest.MonkeyPatch,
    error: RuntimeError,
    status_code: int,
    detail: object,
) -> None:
    async def fail(*_args: object, **_kwargs: object) -> Watch:
        raise error

    monkeypatch.setattr(services_module, "create_watch_application", fail)

    with pytest.raises(HTTPException) as raised:
        await create_watch(cast(AsyncSession, object()), make_data())

    assert raised.value.status_code == status_code
    assert raised.value.detail == detail


async def test_services_create_wrapper_does_not_translate_unrelated_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail(*_args: object, **_kwargs: object) -> Watch:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(services_module, "create_watch_application", fail)

    with pytest.raises(RuntimeError, match="database unavailable"):
        await create_watch(cast(AsyncSession, object()), make_data())
