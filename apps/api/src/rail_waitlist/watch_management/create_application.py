from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import Provider, SeatObservationMode, SeatObservationStatus, WatchStatus
from ..official_rail_identity import normalize_official_train_number
from ..timetable_management.models import TimetableSeatEvidence
from .models import Watch, WatchCandidate
from .schemas import WatchCreate


class WatchCreateForbidden(RuntimeError):
    """The requested watch mode is disabled by an explicit runtime gate."""


class WatchCreateValidationError(RuntimeError):
    """Persisted evidence or provider scope does not authorize watch creation."""


class WatchRegistrationEvidenceExpired(RuntimeError):
    """The selected timetable evidence expired before the watch was created."""


class RequestHash(Protocol):
    def __call__(self, value: object) -> str: ...


class GetIdempotentResource(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        scope: str,
        key: str | None,
        payload_hash: str,
    ) -> str | None: ...


class EnsureFocusedObservationCapacity(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        provider: Provider,
        *,
        exclude_watch_id: str | None = None,
    ) -> None: ...


class ExperimentalRailEnabled(Protocol):
    def __call__(self) -> bool: ...


class ValidateChannelIds(Protocol):
    async def __call__(self, session: AsyncSession, channel_ids: list[str]) -> None: ...


class BuildWatchDedupeKey(Protocol):
    def __call__(
        self,
        provider: Provider,
        origin: str,
        destination: str,
        travel_date: date,
        time_from: time,
        time_to: time,
        seat_class: str,
        passenger_count: int,
        train_numbers: list[str],
        origin_node_id: str | None = None,
        destination_node_id: str | None = None,
    ) -> str: ...


class OfficialBookingUrlForProvider(Protocol):
    def __call__(self, provider: Provider) -> str: ...


class RememberIdempotency(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        scope: str,
        key: str | None,
        resource_id: str,
        payload_hash: str,
    ) -> None: ...


class AddOutboxEvent(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        *,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, object],
        dedupe_key: str,
    ) -> object: ...


class Clock(Protocol):
    def __call__(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class WatchCreateDependencies:
    request_hash: RequestHash
    get_idempotent_resource: GetIdempotentResource
    ensure_focused_observation_capacity: EnsureFocusedObservationCapacity
    experimental_rail_enabled: ExperimentalRailEnabled
    validate_channel_ids: ValidateChannelIds
    build_watch_dedupe_key: BuildWatchDedupeKey
    official_booking_url_for_provider: OfficialBookingUrlForProvider
    remember_idempotency: RememberIdempotency
    add_outbox_event: AddOutboxEvent
    now: Clock


async def create_watch(
    session: AsyncSession,
    data: WatchCreate,
    idempotency_key: str | None = None,
    *,
    dependencies: WatchCreateDependencies,
) -> Watch:
    """Create one watch aggregate and commit its idempotency/outbox unit of work."""
    payload_hash = dependencies.request_hash(data)
    existing_id = await dependencies.get_idempotent_resource(
        session, "watch.create", idempotency_key, payload_hash
    )
    if existing_id:
        existing = await session.get(Watch, existing_id)
        if existing:
            return existing

    if data.seat_observation_mode is SeatObservationMode.FOCUSED:
        await dependencies.ensure_focused_observation_capacity(session, data.provider)

    if data.mode == "experimental" and not dependencies.experimental_rail_enabled():
        raise WatchCreateForbidden("experimental rail mode is disabled")

    await dependencies.validate_channel_ids(session, data.notification_channel_ids)

    registration_evidence: dict[str, TimetableSeatEvidence] = {}
    if data.provider in {Provider.KORAIL, Provider.SRT}:
        if any(candidate.registration_evidence_id is None for candidate in data.candidates):
            raise WatchCreateValidationError(
                "official watch candidates require registration evidence"
            )
        evidence_ids = {
            candidate.registration_evidence_id
            for candidate in data.candidates
            if candidate.registration_evidence_id is not None
        }
        rows = list(
            (
                await session.scalars(
                    select(TimetableSeatEvidence).where(TimetableSeatEvidence.id.in_(evidence_ids))
                )
            ).all()
        )
        registration_evidence = {row.id: row for row in rows}
        now = dependencies.now()
        for candidate in data.candidates:
            evidence_id = candidate.registration_evidence_id
            evidence = registration_evidence.get(evidence_id or "")
            if evidence is None:
                raise WatchCreateValidationError("registration evidence was not found")
            if (
                not evidence.registration_allowed
                or evidence.status == SeatObservationStatus.UNKNOWN
                or evidence.provenance_kind == "not_observed"
            ):
                raise WatchCreateValidationError(
                    "registration evidence is not eligible for watch creation"
                )
            departure_at = candidate.departure_at.astimezone(UTC).replace(microsecond=0)
            evidence_departure = evidence.departure_at
            if evidence_departure.tzinfo is None or evidence_departure.utcoffset() is None:
                evidence_departure = evidence_departure.replace(tzinfo=UTC)
            valid_until = evidence.registration_valid_until
            if valid_until.tzinfo is None or valid_until.utcoffset() is None:
                valid_until = valid_until.replace(tzinfo=UTC)
            exact_match = (
                evidence.provider == data.provider
                and evidence.origin_node_id == data.origin_node_id
                and evidence.destination_node_id == data.destination_node_id
                and evidence.canonical_train_number
                == normalize_official_train_number(candidate.train_number)
                and evidence_departure.astimezone(UTC).replace(microsecond=0) == departure_at
                and evidence.passenger_count == data.passenger_count
                and evidence.seat_class == candidate.seat_class
            )
            if not exact_match:
                raise WatchCreateValidationError(
                    "registration evidence does not match the watch candidate"
                )
            if valid_until <= now:
                raise WatchRegistrationEvidenceExpired(
                    "좌석 등록 근거가 만료되었습니다. 좌석 상태를 다시 조회해 주세요."
                )
    elif any(candidate.registration_evidence_id is not None for candidate in data.candidates):
        raise WatchCreateValidationError("registration evidence is only valid for official watches")

    dedupe_key = dependencies.build_watch_dedupe_key(
        data.provider,
        data.origin,
        data.destination,
        data.travel_date,
        data.time_from,
        data.time_to,
        data.seat_class,
        data.passenger_count,
        data.train_numbers,
        data.origin_node_id,
        data.destination_node_id,
    )
    watch_values = data.model_dump(exclude={"candidates"})
    watch = Watch(
        **watch_values,
        status=WatchStatus.DRAFT,
        dedupe_key=dedupe_key,
        official_booking_url=dependencies.official_booking_url_for_provider(data.provider),
    )
    watch.candidates = []
    for candidate in data.candidates:
        candidate_values = candidate.model_dump()
        candidate_values["departure_at"] = candidate.departure_at.astimezone(UTC)
        candidate_values["scheduled_departure_at"] = candidate.departure_at.astimezone(UTC)
        if candidate.arrival_at is not None:
            candidate_values["arrival_at"] = candidate.arrival_at.astimezone(UTC)
        persisted_candidate = WatchCandidate(**candidate_values)
        if candidate.registration_evidence_id is not None:
            persisted_candidate.registration_evidence = registration_evidence[
                candidate.registration_evidence_id
            ]
        watch.candidates.append(persisted_candidate)
    session.add(watch)
    await session.flush()
    try:
        await dependencies.remember_idempotency(
            session, "watch.create", idempotency_key, watch.id, payload_hash
        )
        await dependencies.add_outbox_event(
            session,
            aggregate_type="watch",
            aggregate_id=watch.id,
            event_type="watch.created",
            payload={"watch_id": watch.id, "status": watch.status.value},
            dedupe_key=f"watch:{watch.id}:created",
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        if idempotency_key:
            existing_id = await dependencies.get_idempotent_resource(
                session, "watch.create", idempotency_key, payload_hash
            )
            if existing_id:
                existing = await session.get(Watch, existing_id)
                if existing is not None:
                    return existing
        raise
    await session.refresh(watch)
    return watch
