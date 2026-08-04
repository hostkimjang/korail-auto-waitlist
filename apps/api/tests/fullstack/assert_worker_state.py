from __future__ import annotations

import asyncio
import json

from sqlalchemy import func, select

from rail_waitlist.database import SessionFactory
from rail_waitlist.domain import (
    OutboxStatus,
    Provider,
    SeatObservationStatus,
    WatchStatus,
)
from rail_waitlist.models import (
    OutboxEvent,
    ProviderExecutionLease,
    ReservationAttempt,
    SeatObservation,
    TimetableSeatEvidence,
    Watch,
    WatchCandidate,
    WatchTransitionHistory,
)

EXPECTED = {
    ("9002", "standard"): (WatchStatus.SEAT_FOUND, SeatObservationStatus.AVAILABLE),
    ("9002", "first"): (WatchStatus.WATCHING, SeatObservationStatus.SOLD_OUT),
    ("9003", "standard"): (
        WatchStatus.OFFICIAL_WAITLIST,
        SeatObservationStatus.WAITLIST_AVAILABLE,
    ),
}
KORAIL_EXPECTED = ("9001", "first")


async def _snapshot() -> dict[str, object] | None:
    async with SessionFactory() as session:
        korail_evidence = list(
            (
                await session.scalars(
                    select(TimetableSeatEvidence).where(
                        TimetableSeatEvidence.provider == Provider.KORAIL,
                        TimetableSeatEvidence.canonical_train_number == "9001",
                    )
                )
            ).all()
        )
        if len(korail_evidence) != 1:
            return None

        rows = list(
            (
                await session.execute(
                    select(Watch, WatchCandidate)
                    .join(WatchCandidate, WatchCandidate.watch_id == Watch.id)
                    .where(Watch.provider == Provider.SRT)
                )
            ).all()
        )
        indexed = {
            (candidate.train_number.lstrip("0") or "0", candidate.seat_class): (
                watch,
                candidate,
            )
            for watch, candidate in rows
        }
        if set(indexed) != set(EXPECTED):
            return None

        korail_row = (
            await session.execute(
                select(Watch, WatchCandidate)
                .join(WatchCandidate, WatchCandidate.watch_id == Watch.id)
                .where(
                    Watch.provider == Provider.KORAIL,
                    WatchCandidate.train_number == KORAIL_EXPECTED[0],
                    WatchCandidate.seat_class == KORAIL_EXPECTED[1],
                )
            )
        ).one_or_none()
        if korail_row is None:
            return None
        korail_watch, korail_candidate = korail_row
        korail_observation = await session.scalar(
            select(SeatObservation)
            .where(SeatObservation.candidate_id == korail_candidate.id)
            .order_by(SeatObservation.observed_at.desc())
            .limit(1)
        )
        if korail_observation is None:
            return None
        if (
            korail_watch.status != WatchStatus.WATCHING
            or korail_observation.status != SeatObservationStatus.SOLD_OUT
            or korail_observation.source != "korail-official-page-browser"
            or korail_observation.error_category is not None
            or korail_watch.next_check_at is None
        ):
            return None

        observations: dict[tuple[str, str], SeatObservation] = {}
        for identity, (_, candidate) in indexed.items():
            latest = await session.scalar(
                select(SeatObservation)
                .where(SeatObservation.candidate_id == candidate.id)
                .order_by(SeatObservation.observed_at.desc())
                .limit(1)
            )
            if latest is None:
                return None
            observations[identity] = latest

        for identity, (expected_watch, expected_observation) in EXPECTED.items():
            watch, _ = indexed[identity]
            observation = observations[identity]
            if watch.status != expected_watch or observation.status != expected_observation:
                return None
            if observation.source != "fullstack-srt-fixture":
                raise AssertionError("full-stack observations must retain fixture provenance")
            if observation.error_category is not None:
                raise AssertionError("deterministic observations must not carry an error")
            if expected_watch == WatchStatus.WATCHING and watch.next_check_at is None:
                raise AssertionError("sold-out watch must keep its next observation time")
            if expected_watch != WatchStatus.WATCHING and watch.next_check_at is not None:
                raise AssertionError("actionable terminal handoff states must stop polling")

        transitions = list(
            (
                await session.scalars(
                    select(WatchTransitionHistory).where(
                        WatchTransitionHistory.to_status.in_(
                            [WatchStatus.SEAT_FOUND, WatchStatus.OFFICIAL_WAITLIST]
                        )
                    )
                )
            ).all()
        )
        if len(transitions) != 2 or any(item.observation_id is None for item in transitions):
            raise AssertionError("actionable transitions must reference their observation")

        reservation_attempts = await session.scalar(
            select(func.count()).select_from(ReservationAttempt)
        )
        if reservation_attempts != 0:
            raise AssertionError("SRT observation must not create reservation attempts")

        lease = await session.get(
            ProviderExecutionLease,
            {"provider": Provider.SRT, "account_scope": "anonymous/public"},
        )
        if lease is None or lease.fencing_token < 1:
            return None
        if lease.owner_token is not None or lease.expires_at is not None:
            return None
        korail_lease = await session.get(
            ProviderExecutionLease,
            {"provider": Provider.KORAIL, "account_scope": "anonymous/public"},
        )
        if korail_lease is None or korail_lease.fencing_token < 1:
            return None
        if korail_lease.owner_token is not None or korail_lease.expires_at is not None:
            return None

        notifications = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "notification.dispatch_requested"
                    )
                )
            ).all()
        )
        if len(notifications) != 2:
            return None
        notification_statuses = {item.payload.get("status") for item in notifications}
        if notification_statuses != {"seat_found", "official_waitlist"}:
            raise AssertionError("notification outbox must match actionable watch states")
        if len({item.dedupe_key for item in notifications}) != 2:
            raise AssertionError("notification outbox dedupe keys must be unique")
        if any(item.attempts < 1 for item in notifications):
            return None
        if any(
            item.status not in {OutboxStatus.PENDING, OutboxStatus.FAILED} for item in notifications
        ):
            raise AssertionError("isolated notifications must fail closed without external egress")

        seat_events = await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.event_type == "watch.seat_observed")
        )
        if int(seat_events or 0) != 4:
            raise AssertionError("one normalized seat event is required per watch")

        return {
            "korail_browser_evidence": len(korail_evidence),
            "korail_watch_status": korail_watch.status.value,
            "korail_observation_status": korail_observation.status.value,
            "korail_lease_fencing_token": korail_lease.fencing_token,
            "watch_statuses": sorted(status.value for status, _ in EXPECTED.values()),
            "observation_statuses": sorted(status.value for _, status in EXPECTED.values()),
            "lease_fencing_token": lease.fencing_token,
            "lease_released": True,
            "notification_events": len(notifications),
            "notification_attempts": sorted(item.attempts for item in notifications),
            "reservation_attempts": reservation_attempts,
        }


async def _diagnostic() -> dict[str, object]:
    async with SessionFactory() as session:
        rows = list(
            (
                await session.execute(
                    select(Watch.status, WatchCandidate.train_number, WatchCandidate.seat_class)
                    .join(WatchCandidate, WatchCandidate.watch_id == Watch.id)
                    .where(Watch.provider == Provider.SRT)
                    .order_by(WatchCandidate.train_number, WatchCandidate.seat_class)
                )
            ).all()
        )
        observations = list(
            (
                await session.execute(
                    select(
                        WatchCandidate.train_number,
                        WatchCandidate.seat_class,
                        SeatObservation.status,
                        SeatObservation.source,
                        SeatObservation.error_category,
                    )
                    .join(
                        SeatObservation,
                        SeatObservation.candidate_id == WatchCandidate.id,
                    )
                    .order_by(SeatObservation.observed_at)
                )
            ).all()
        )
        lease = await session.get(
            ProviderExecutionLease,
            {"provider": Provider.SRT, "account_scope": "anonymous/public"},
        )
        korail_evidence = list(
            (
                await session.execute(
                    select(
                        TimetableSeatEvidence.canonical_train_number,
                        TimetableSeatEvidence.seat_class,
                        TimetableSeatEvidence.status,
                        TimetableSeatEvidence.provenance_kind,
                        TimetableSeatEvidence.source,
                    )
                    .where(TimetableSeatEvidence.provider == Provider.KORAIL)
                    .order_by(
                        TimetableSeatEvidence.canonical_train_number,
                        TimetableSeatEvidence.seat_class,
                    )
                )
            ).all()
        )
        korail_rows = list(
            (
                await session.execute(
                    select(Watch.status, WatchCandidate.train_number, WatchCandidate.seat_class)
                    .join(WatchCandidate, WatchCandidate.watch_id == Watch.id)
                    .where(Watch.provider == Provider.KORAIL)
                    .order_by(WatchCandidate.train_number, WatchCandidate.seat_class)
                )
            ).all()
        )
        korail_lease = await session.get(
            ProviderExecutionLease,
            {"provider": Provider.KORAIL, "account_scope": "anonymous/public"},
        )
        return {
            "watches": [
                [status.value, train_number, seat_class]
                for status, train_number, seat_class in rows
            ],
            "observations": [
                [train_number, seat_class, status.value, source, error_category]
                for train_number, seat_class, status, source, error_category in observations
            ],
            "lease": None
            if lease is None
            else {
                "fencing_token": lease.fencing_token,
                "released": lease.owner_token is None and lease.expires_at is None,
            },
            "korail_evidence": [
                [train_number, seat_class.value, status.value, provenance_kind, source]
                for train_number, seat_class, status, provenance_kind, source in korail_evidence
            ],
            "korail_watches": [
                [status.value, train_number, seat_class]
                for status, train_number, seat_class in korail_rows
            ],
            "korail_lease": None
            if korail_lease is None
            else {
                "fencing_token": korail_lease.fencing_token,
                "released": (korail_lease.owner_token is None and korail_lease.expires_at is None),
            },
        }


async def main() -> None:
    for _ in range(40):
        snapshot = await _snapshot()
        if snapshot is not None:
            print(json.dumps(snapshot, sort_keys=True))
            return
        await asyncio.sleep(1)
    print(json.dumps(await _diagnostic(), sort_keys=True))
    raise AssertionError("worker full-stack state did not converge within 40 seconds")


if __name__ == "__main__":
    asyncio.run(main())
