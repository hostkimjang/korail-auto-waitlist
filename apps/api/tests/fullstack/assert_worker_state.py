from __future__ import annotations

import asyncio
import json

from sqlalchemy import func, select

from rail_waitlist.database import SessionFactory
from rail_waitlist.domain import (
    OutboxStatus,
    Provider,
    SeatClass,
    SeatObservationStatus,
    WatchStatus,
)
from rail_waitlist.models import (
    OutboxEvent,
    ProviderExecutionLease,
    ReservationAttempt,
    SeatObservation,
    Watch,
    WatchCandidate,
    WatchTransitionHistory,
)
from rail_waitlist.timetable_management.models import TimetableSeatEvidence

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
        expected_korail_evidence = {
            (SeatClass.STANDARD, SeatObservationStatus.AVAILABLE),
            (SeatClass.FIRST, SeatObservationStatus.SOLD_OUT),
        }
        if {
            (evidence.seat_class, evidence.status) for evidence in korail_evidence
        } != expected_korail_evidence:
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
            if watch.next_check_at is None:
                raise AssertionError("active watches must keep their next observation time")

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
        expected_summary_transitions = {
            (
                WatchStatus.SEAT_FOUND,
                "authorized_seat_observation_summary_seat_found",
                observations[("9002", "standard")].id,
            ),
            (
                WatchStatus.OFFICIAL_WAITLIST,
                "authorized_seat_observation_summary_official_waitlist",
                observations[("9003", "standard")].id,
            ),
        }
        actual_summary_transitions = {
            (item.to_status, item.reason, item.observation_id) for item in transitions
        }
        if len(transitions) != 2 or actual_summary_transitions != expected_summary_transitions:
            raise AssertionError("actionable group summaries must retain their transition contract")

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
        korail_lease = await session.get(
            ProviderExecutionLease,
            {"provider": Provider.KORAIL, "account_scope": "anonymous/public"},
        )
        if korail_lease is None or korail_lease.fencing_token < 1:
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
        if any(item.attempts != 0 for item in notifications):
            raise AssertionError("isolated notifications must not attempt external delivery")
        if any(item.status is not OutboxStatus.PENDING for item in notifications):
            raise AssertionError("isolated notifications must remain pending without a consumer")

        observation_count, seat_events = (
            await session.execute(
                select(
                    select(func.count()).select_from(SeatObservation).scalar_subquery(),
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(OutboxEvent.event_type == "watch.seat_observed")
                    .scalar_subquery(),
                )
            )
        ).one()
        if observation_count < 4 or seat_events != observation_count:
            raise AssertionError("every normalized observation must retain its seat event")

        return {
            "korail_browser_evidence": len(korail_evidence),
            "korail_watch_status": korail_watch.status.value,
            "korail_observation_status": korail_observation.status.value,
            "korail_lease_fencing_token": korail_lease.fencing_token,
            "watch_statuses": sorted(status.value for status, _ in EXPECTED.values()),
            "observation_statuses": sorted(status.value for _, status in EXPECTED.values()),
            "lease_fencing_token": lease.fencing_token,
            "lease_released": lease.owner_token is None and lease.expires_at is None,
            "korail_lease_released": (
                korail_lease.owner_token is None and korail_lease.expires_at is None
            ),
            "observation_events": seat_events,
            "notification_events": len(notifications),
            "notification_attempts": sorted(item.attempts for item in notifications),
            "reservation_attempts": reservation_attempts,
        }


async def _diagnostic() -> dict[str, object]:
    async with SessionFactory() as session:
        transitions = list(
            (
                await session.execute(
                    select(
                        WatchTransitionHistory.to_status,
                        WatchTransitionHistory.reason,
                        WatchTransitionHistory.observation_id,
                    ).where(
                        WatchTransitionHistory.to_status.in_(
                            [WatchStatus.SEAT_FOUND, WatchStatus.OFFICIAL_WAITLIST]
                        )
                    )
                )
            ).all()
        )
        notifications = list(
            (
                await session.execute(
                    select(
                        OutboxEvent.status,
                        OutboxEvent.attempts,
                    ).where(OutboxEvent.event_type == "notification.dispatch_requested")
                )
            ).all()
        )
        observation_count, seat_events = (
            await session.execute(
                select(
                    select(func.count()).select_from(SeatObservation).scalar_subquery(),
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(OutboxEvent.event_type == "watch.seat_observed")
                    .scalar_subquery(),
                )
            )
        ).one()
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
            "actionable_transitions": [
                [status.value, reason, observation_id]
                for status, reason, observation_id in transitions
            ],
            "notifications": [[status.value, attempts] for status, attempts in notifications],
            "observation_count": observation_count,
            "seat_events": seat_events,
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
