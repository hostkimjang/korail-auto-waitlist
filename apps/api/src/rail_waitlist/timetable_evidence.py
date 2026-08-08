from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .domain import Provider, SeatObservationStatus
from .official_rail_identity import normalize_official_train_number
from .timetable_management.models import TimetableSeatEvidence
from .timetable_management.schemas import TimetableItem

REGISTRATION_WINDOW = timedelta(minutes=5)


def _can_add_to_watch(item: TimetableItem, seat_index: int) -> bool:
    seat = item.seat_classes[seat_index]
    return (
        seat.status != "unknown"
        and seat.provenance.kind not in {"not_observed", "mock"}
        and any(action.kind == "add_to_watch" for action in seat.actions)
    )


def _utc_second(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(microsecond=0)


def _evidence_hash(values: dict[str, object]) -> str:
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _issuance_bucket(value: datetime) -> datetime:
    return _utc_second(value).replace(second=0)


def _registration_valid_until(
    item: TimetableItem, seat_index: int, issued_at: datetime
) -> datetime:
    provenance = item.seat_classes[seat_index].provenance
    bounded_until = issued_at + REGISTRATION_WINDOW
    if provenance.kind in {
        "user_confirmed_official_page",
        "official_page_browser_companion",
    }:
        assert provenance.fresh_until is not None
        return min(bounded_until, _utc_second(provenance.fresh_until))
    if provenance.kind == "official_provider":
        assert provenance.observed_at is not None
        freshness_limit = provenance.fresh_until or provenance.observed_at + REGISTRATION_WINDOW
        return min(bounded_until, _utc_second(freshness_limit))
    return bounded_until


async def persist_timetable_seat_evidence(
    session: AsyncSession,
    items: list[TimetableItem],
    *,
    provider: Provider,
    origin_node_id: str,
    destination_node_id: str,
    passenger_count: int,
    now: datetime | None = None,
) -> list[TimetableItem]:
    """Issue immutable registration evidence without affecting watch or worker state."""
    if provider not in {Provider.KORAIL, Provider.SRT}:
        return items
    created_at = _issuance_bucket(now or datetime.now(UTC))
    result: list[TimetableItem] = []
    for item in items:
        seats = []
        for seat_index, seat in enumerate(item.seat_classes):
            if not _can_add_to_watch(item, seat_index):
                # Registration is fail-closed: a rendered unknown, demo, or
                # non-actionable seat must never become a server-side watch.
                seats.append(seat.model_copy(update={"registration_evidence_id": None}))
                continue
            valid_until = _registration_valid_until(item, seat_index, created_at)
            if valid_until <= created_at:
                # Stale evidence remains visible on the timetable but cannot mint a
                # registration token.
                seats.append(seat.model_copy(update={"registration_evidence_id": None}))
                continue
            departure_at = _utc_second(item.departure_at)
            observed_at = (
                _utc_second(seat.provenance.observed_at)
                if seat.provenance.observed_at is not None
                else None
            )
            fresh_until = (
                _utc_second(seat.provenance.fresh_until)
                if seat.provenance.fresh_until is not None
                else None
            )
            hash_values = {
                "provider": provider.value,
                "origin_node_id": origin_node_id,
                "destination_node_id": destination_node_id,
                "canonical_train_number": normalize_official_train_number(item.train_number),
                "departure_at": departure_at.isoformat(),
                "passenger_count": passenger_count,
                "seat_class": seat.seat_class.value,
                "status": seat.status,
                "provenance_kind": seat.provenance.kind,
                "source": seat.provenance.source,
                "observed_at": observed_at.isoformat() if observed_at else None,
                "fresh_until": fresh_until.isoformat() if fresh_until else None,
                "reason": seat.provenance.reason,
                "registration_allowed": True,
                "registration_valid_until": valid_until.isoformat(),
            }
            evidence_hash = _evidence_hash(hash_values)
            evidence = await session.scalar(
                select(TimetableSeatEvidence).where(
                    TimetableSeatEvidence.evidence_hash == evidence_hash
                )
            )
            if evidence is None:
                evidence = TimetableSeatEvidence(
                    evidence_hash=evidence_hash,
                    provider=provider,
                    origin_node_id=origin_node_id,
                    destination_node_id=destination_node_id,
                    canonical_train_number=hash_values["canonical_train_number"],
                    departure_at=departure_at,
                    passenger_count=passenger_count,
                    seat_class=seat.seat_class,
                    status=SeatObservationStatus(seat.status),
                    provenance_kind=seat.provenance.kind,
                    source=seat.provenance.source,
                    observed_at=observed_at,
                    fresh_until=fresh_until,
                    reason=seat.provenance.reason,
                    registration_allowed=True,
                    created_at=created_at,
                    registration_valid_until=valid_until,
                )
                try:
                    async with session.begin_nested():
                        session.add(evidence)
                        await session.flush()
                except IntegrityError:
                    evidence = await session.scalar(
                        select(TimetableSeatEvidence).where(
                            TimetableSeatEvidence.evidence_hash == evidence_hash
                        )
                    )
                    if evidence is None:
                        raise
            seats.append(seat.model_copy(update={"registration_evidence_id": evidence.id}))
        result.append(item.model_copy(update={"seat_classes": seats}))
    return result
