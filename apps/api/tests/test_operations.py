from __future__ import annotations

import uuid
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from rail_waitlist.domain import (
    OutboxStatus,
    Provider,
    ProviderCircuitState,
    ReservationOutcome,
    ReservationPolicy,
    SeatObservationStatus,
    WatchStatus,
)
from rail_waitlist.models import (
    OutboxEvent,
    ProviderCircuit,
    ReservationAttempt,
    SeatObservation,
    StationCatalogCache,
    Watch,
    WatchCandidate,
    WatchTransitionHistory,
)
from rail_waitlist.operation_summary.schemas import (
    OperationsSummary as FeatureOperationsSummary,
)
from rail_waitlist.operations import RESERVATION_REASON_CODES
from rail_waitlist.reservations.provider_confirmation.contracts import (
    ReservationConfirmationOutcome,
)
from rail_waitlist.schemas import OperationsSummary as CompatibilityOperationsSummary


def test_operations_summary_schema_keeps_compatibility_export():
    assert CompatibilityOperationsSummary is FeatureOperationsSummary


def test_operations_summary_has_a_closed_reason_for_every_reservation_outcome():
    assert RESERVATION_REASON_CODES == {
        ReservationOutcome.PENDING: "reservation_pending",
        ReservationOutcome.PAYMENT_REQUIRED: "reservation_payment_required",
        ReservationOutcome.RESERVED: "reservation_reserved",
        ReservationOutcome.NOT_AVAILABLE: "reservation_not_available",
        ReservationOutcome.AUTH_REQUIRED: "reservation_auth_required",
        ReservationOutcome.PROVIDER_BLOCKED: "reservation_provider_blocked",
        ReservationOutcome.FAILED: "reservation_failed",
        ReservationOutcome.UNKNOWN: "reservation_unknown",
    }


async def test_operations_summary_requires_admin_session(public_client):
    response = await public_client.get("/api/v1/operations/summary")
    assert response.status_code == 401


async def test_operations_summary_is_source_backed_and_sanitized(app, client):
    now = datetime.now(UTC)
    secret_marker = "private.example/token/admin-42"
    watch = Watch(
        id=str(uuid.uuid4()),
        provider=Provider.MOCK,
        origin="비공개 출발역",
        destination="비공개 도착역",
        travel_date=now.date() + timedelta(days=1),
        time_from=time(8),
        time_to=time(12),
        seat_class="standard",
        passenger_count=1,
        train_numbers=["KTX-77"],
        notification_channel_ids=[],
        mode="mock",
        status=WatchStatus.FAILED,
        dedupe_key="private-dedupe-key",
        created_at=now - timedelta(hours=2),
        updated_at=now - timedelta(minutes=10),
    )
    candidate = WatchCandidate(
        id=str(uuid.uuid4()),
        watch=watch,
        train_number="KTX-77",
        departure_at=now + timedelta(days=1),
        seat_class="standard",
        priority=1,
    )
    observations = [
        SeatObservation(
            candidate=candidate,
            status=SeatObservationStatus.ERROR,
            source=secret_marker,
            observed_at=now - timedelta(minutes=30),
            fresh_until=now - timedelta(minutes=20),
            error_category="timeout",
        ),
        SeatObservation(
            candidate=candidate,
            status=SeatObservationStatus.ERROR,
            source=secret_marker,
            observed_at=now - timedelta(minutes=20),
            fresh_until=now - timedelta(minutes=10),
            error_category=secret_marker,
        ),
        SeatObservation(
            candidate=candidate,
            status=SeatObservationStatus.AVAILABLE,
            source=secret_marker,
            observed_at=now - timedelta(minutes=10),
            fresh_until=now + timedelta(minutes=5),
        ),
        SeatObservation(
            candidate=candidate,
            status=SeatObservationStatus.ERROR,
            source=secret_marker,
            observed_at=now - timedelta(hours=25),
            fresh_until=now - timedelta(hours=24, minutes=50),
            error_category="timeout",
        ),
    ]
    attempt = ReservationAttempt(
        candidate=candidate,
        idempotency_key="private-attempt-key",
        started_at=now - timedelta(minutes=8),
        finished_at=now - timedelta(minutes=7),
        outcome=ReservationOutcome.FAILED,
        official_handoff_url=f"https://{secret_marker}",
    )
    transition = WatchTransitionHistory(
        watch=watch,
        from_status=WatchStatus.WATCHING,
        to_status=WatchStatus.FAILED,
        reason=secret_marker,
        created_at=now - timedelta(minutes=6),
    )
    outbox = [
        OutboxEvent(
            aggregate_type="notification_channel",
            aggregate_id="private-channel-sent",
            event_type="notification.dispatch_requested",
            payload={"request_body": secret_marker},
            dedupe_key="private-outbox-sent",
            status=OutboxStatus.SENT,
            attempts=1,
            processed_at=now - timedelta(minutes=5),
            created_at=now - timedelta(minutes=6),
        ),
        OutboxEvent(
            aggregate_type="notification_channel",
            aggregate_id="private-channel-failed",
            event_type="notification.test_requested",
            payload={"request_body": secret_marker},
            dedupe_key="private-outbox-failed",
            status=OutboxStatus.FAILED,
            attempts=5,
            last_error=secret_marker,
            processed_at=now - timedelta(minutes=4),
            created_at=now - timedelta(hours=25),
        ),
        OutboxEvent(
            aggregate_type="notification_channel",
            aggregate_id="private-channel-pending",
            event_type="notification.test_requested",
            payload={"request_body": secret_marker},
            dedupe_key="private-outbox-pending",
            status=OutboxStatus.PENDING,
            attempts=1,
            last_error=secret_marker,
            created_at=now - timedelta(minutes=3),
        ),
        OutboxEvent(
            aggregate_type="watch",
            aggregate_id=watch.id,
            event_type="watch.status_changed",
            payload={"watch_id": watch.id, "secret": secret_marker},
            dedupe_key="private-non-notification-event",
            status=OutboxStatus.PENDING,
            created_at=now - timedelta(minutes=2),
        ),
    ]
    circuit = ProviderCircuit(
        provider=Provider.MOCK,
        state=ProviderCircuitState.MANUAL_HOLD,
        reason=secret_marker,
        manual_resume_required=True,
        generation=3,
        updated_at=now - timedelta(minutes=1),
    )
    catalog = StationCatalogCache(
        cache_key="tago_station_catalog_all",
        schema_version=2,
        station_count=0,
        retrieved_at=now - timedelta(hours=1),
        last_error_category=secret_marker,
        updated_at=now - timedelta(minutes=1),
    )

    async with app.state.test_session_factory() as session:
        session.add_all(
            [watch, candidate, *observations, attempt, transition, *outbox, circuit, catalog]
        )
        await session.commit()
        watch_id = watch.id
        candidate_id = candidate.id

    response = await client.get("/api/v1/operations/summary")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()

    assert payload["window"]["hours"] == 24
    assert payload["seat_observation_error_rate"] == {
        "numerator": 2,
        "denominator": 3,
        "rate": 2 / 3,
        "definition": (
            "24시간 좌석 관측 오류율: status=error인 seat_observations / "
            "같은 기간의 전체 seat_observations. 서버·HTTP 오류율이 아닙니다."
        ),
    }
    assert payload["notification_delivery_failure_rate"]["numerator"] == 1
    assert payload["notification_delivery_failure_rate"]["denominator"] == 2
    assert payload["notification_delivery_failure_rate"]["rate"] == 0.5
    assert payload["notification_delivery_failure_rate"]["definition"] == (
        "24시간 알림 전달 최종 실패율: processed_at이 기간 안인 failed / "
        "(sent + failed). pending은 분모에서 제외합니다."
    )
    assert payload["current_counts"]["notification_outbox_pending"] == 1
    assert payload["window_counts"]["reservation_failures"] == 1
    assert payload["window_counts"]["watch_failure_transitions"] == 1
    assert payload["window_counts"]["notification_events"] == 2

    services = {item["service"]: item for item in payload["services"]}
    assert services["api"]["status"] == "healthy"
    assert services["database"]["status"] == "healthy"
    assert services["worker"] == {
        "service": "worker",
        "status": "unknown",
        "observed_at": None,
        "evidence": "durable_heartbeat_unavailable",
    }
    assert services["scheduler"]["status"] == "unknown"
    assert payload["provider_circuits"][0]["state"] == "manual_hold"
    assert any(
        entry["kind"] == "seat_observation" and entry["error_category"] == "unknown"
        for entry in payload["recent_entries"]
    )
    contextual_entries = [
        entry
        for entry in payload["recent_entries"]
        if entry["kind"] in {"seat_observation", "reservation_attempt"}
    ]
    assert contextual_entries
    assert all(entry["train_number"] == "KTX-77" for entry in contextual_entries)
    assert all(entry["departure_at"].endswith("+09:00") for entry in contextual_entries)
    assert all(entry["seat_class"] == "standard" for entry in contextual_entries)
    failed_attempt_entry = next(
        entry for entry in contextual_entries if entry["kind"] == "reservation_attempt"
    )
    assert failed_attempt_entry["reason_code"] == "reservation_failed"

    serialized = response.text
    for forbidden in (
        secret_marker,
        watch_id,
        candidate_id,
        "비공개 출발역",
        "비공개 도착역",
        "private-channel",
        "request_body",
        "last_error",
        "official_handoff_url",
    ):
        assert forbidden not in serialized


async def test_recent_entries_exclude_routine_observations_before_source_limit(app, client):
    now = datetime.now(UTC)
    watch = Watch(
        id=str(uuid.uuid4()),
        provider=Provider.KORAIL,
        origin="서울",
        destination="부산",
        travel_date=(now + timedelta(days=1)).date(),
        time_from=time(8),
        time_to=time(12),
        seat_class="standard",
        passenger_count=1,
        train_numbers=["101"],
        notification_channel_ids=[],
        mode="real",
        status=WatchStatus.AUTH_REQUIRED,
        dedupe_key="recent-entry-observation-filter",
        created_at=now - timedelta(hours=2),
        updated_at=now - timedelta(minutes=1),
    )
    candidate = WatchCandidate(
        id=str(uuid.uuid4()),
        watch=watch,
        train_number="101",
        departure_at=now + timedelta(days=1),
        seat_class="standard",
        priority=1,
    )
    sold_out_noise = [
        SeatObservation(
            candidate=candidate,
            status=SeatObservationStatus.SOLD_OUT,
            source="official_provider",
            observed_at=now - timedelta(seconds=index),
            fresh_until=now + timedelta(minutes=5),
        )
        for index in range(1, 46)
    ]
    routine_statuses = [
        SeatObservationStatus.UNAVAILABLE,
        SeatObservationStatus.AVAILABLE,
        SeatObservationStatus.LIMITED,
        SeatObservationStatus.STANDING_PLUS_SEAT,
        SeatObservationStatus.NOT_ENOUGH_SEATS,
        SeatObservationStatus.SOLD_OUT,
        SeatObservationStatus.WAITLIST_AVAILABLE,
        SeatObservationStatus.RESERVATION_COMPLETED,
        SeatObservationStatus.NOT_OFFERED,
        SeatObservationStatus.DEPARTED,
        SeatObservationStatus.OUT_OF_SERVICE,
    ]
    routine_observations = [
        SeatObservation(
            candidate=candidate,
            status=status,
            source="official_provider",
            observed_at=now - timedelta(minutes=30, seconds=index),
            fresh_until=now - timedelta(minutes=20),
        )
        for index, status in enumerate(routine_statuses)
    ]
    noteworthy_observations = [
        SeatObservation(
            candidate=candidate,
            status=SeatObservationStatus.ERROR,
            source="official_provider",
            observed_at=now - timedelta(minutes=20),
            fresh_until=now - timedelta(minutes=19),
            error_category="timeout",
        ),
        SeatObservation(
            candidate=candidate,
            status=SeatObservationStatus.UNKNOWN,
            source="official_provider",
            observed_at=now - timedelta(minutes=21),
            fresh_until=now - timedelta(minutes=20),
        ),
        SeatObservation(
            candidate=candidate,
            status=SeatObservationStatus.STALE,
            source="official_provider",
            observed_at=now - timedelta(minutes=22),
            fresh_until=now - timedelta(minutes=21),
        ),
    ]
    auth_attempt = ReservationAttempt(
        candidate=candidate,
        idempotency_key="recent-entry-auth-attempt",
        started_at=now - timedelta(minutes=19),
        finished_at=now - timedelta(minutes=18),
        outcome=ReservationOutcome.AUTH_REQUIRED,
    )

    async with app.state.test_session_factory() as session:
        session.add_all(
            [
                watch,
                candidate,
                *sold_out_noise,
                *routine_observations,
                *noteworthy_observations,
                auth_attempt,
            ]
        )
        await session.commit()

    payload = (await client.get("/api/v1/operations/summary")).json()
    observation_entries = [
        entry for entry in payload["recent_entries"] if entry["kind"] == "seat_observation"
    ]

    assert {entry["status"] for entry in observation_entries} == {"error", "unknown", "stale"}
    assert any(
        entry["kind"] == "reservation_attempt"
        and entry["reason_code"] == "reservation_auth_required"
        for entry in payload["recent_entries"]
    )
    expected_observation_count = (
        len(sold_out_noise) + len(routine_observations) + len(noteworthy_observations)
    )
    assert payload["window_counts"]["seat_observations"] == expected_observation_count
    assert payload["seat_observation_error_rate"]["numerator"] == 1
    assert payload["seat_observation_error_rate"]["denominator"] == expected_observation_count
    observation_freshness = next(
        item for item in payload["source_freshness"] if item["source"] == "seat_observations"
    )
    assert (
        datetime.fromisoformat(observation_freshness["observed_at"])
        == sold_out_noise[0].observed_at
    )


async def test_operations_summary_distinguishes_safe_payment_hold_end_reasons(app, client):
    now = datetime.now(UTC)

    def payment_hold_watch(*, train_number: str, minute_offset: int):
        watch = Watch(
            id=str(uuid.uuid4()),
            provider=Provider.MOCK,
            origin="서울",
            destination="대전",
            travel_date=(now + timedelta(days=1)).date(),
            time_from=time(8),
            time_to=time(12),
            seat_class="standard",
            passenger_count=1,
            train_numbers=[train_number],
            notification_channel_ids=[],
            mode="mock",
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            status=WatchStatus.WATCHING,
            dedupe_key=f"hold-{train_number}",
            created_at=now - timedelta(hours=2),
            updated_at=now - timedelta(minutes=minute_offset),
        )
        candidate = WatchCandidate(
            id=str(uuid.uuid4()),
            watch=watch,
            train_number=train_number,
            departure_at=now + timedelta(days=1, hours=1),
            seat_class="standard",
            priority=1,
            state="observed",
        )
        return watch, candidate

    deadline_watch, deadline_candidate = payment_hold_watch(
        train_number="KTX-101",
        minute_offset=7,
    )
    deadline_attempt = ReservationAttempt(
        candidate=deadline_candidate,
        idempotency_key="deadline-attempt",
        started_at=now - timedelta(hours=1),
        finished_at=now - timedelta(minutes=30),
        outcome=ReservationOutcome.PAYMENT_REQUIRED,
        payment_deadline=now - timedelta(minutes=10),
        confirmation_outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
        confirmation_source="safe-test-source",
        confirmation_observed_at=now - timedelta(minutes=7),
        post_deadline_reconciled_at=now - timedelta(minutes=7),
    )
    absent_watch, absent_candidate = payment_hold_watch(
        train_number="KTX-202",
        minute_offset=6,
    )
    absent_attempt = ReservationAttempt(
        candidate=absent_candidate,
        idempotency_key="absent-attempt",
        started_at=now - timedelta(hours=1),
        finished_at=now - timedelta(minutes=25),
        outcome=ReservationOutcome.PAYMENT_REQUIRED,
        confirmation_outcome=ReservationConfirmationOutcome.NOT_FOUND,
        confirmation_source="safe-test-source",
        confirmation_observed_at=now - timedelta(minutes=6),
        post_deadline_reconciled_at=now - timedelta(minutes=6),
    )
    raw_transition_reason = "confirmed_payment_hold_no_longer_actionable_monitoring_resumed"
    transitions = [
        WatchTransitionHistory(
            watch=deadline_watch,
            from_status=WatchStatus.PAYMENT_REQUIRED,
            to_status=WatchStatus.WATCHING,
            reason=raw_transition_reason,
            created_at=now - timedelta(minutes=7),
        ),
        WatchTransitionHistory(
            watch=absent_watch,
            from_status=WatchStatus.PAYMENT_REQUIRED,
            to_status=WatchStatus.WATCHING,
            reason=raw_transition_reason,
            created_at=now - timedelta(minutes=6),
        ),
    ]
    # Historical operation entries must follow the durable transition, not a
    # reservation policy that an administrator may change afterward.
    deadline_watch.reservation_policy = ReservationPolicy.NOTIFY_ONLY
    absent_watch.reservation_policy = ReservationPolicy.NOTIFY_ONLY

    async with app.state.test_session_factory() as session:
        session.add_all(
            [
                deadline_watch,
                deadline_candidate,
                deadline_attempt,
                absent_watch,
                absent_candidate,
                absent_attempt,
                *transitions,
            ]
        )
        await session.commit()

    payload = (await client.get("/api/v1/operations/summary")).json()
    hold_entries = [
        entry
        for entry in payload["recent_entries"]
        if entry["reason_code"]
        in {
            "payment_deadline_elapsed_monitoring_resumed",
            "payment_hold_no_longer_present_monitoring_resumed",
        }
    ]

    assert {entry["reason_code"] for entry in hold_entries} == {
        "payment_deadline_elapsed_monitoring_resumed",
        "payment_hold_no_longer_present_monitoring_resumed",
    }
    assert {entry["train_number"] for entry in hold_entries} == {"KTX-101", "KTX-202"}
    assert all(entry["status"] == "watching" for entry in hold_entries)
    assert all(entry["departure_at"].endswith("+09:00") for entry in hold_entries)
    assert raw_transition_reason not in str(payload)


async def test_operations_summary_projects_confirmed_payment_without_raw_transition_reason(
    app,
    client,
):
    now = datetime.now(UTC)
    watch = Watch(
        id=str(uuid.uuid4()),
        provider=Provider.SRT,
        origin="수서",
        destination="대전",
        travel_date=(now + timedelta(days=2)).date(),
        time_from=time(9),
        time_to=time(12),
        seat_class="first",
        passenger_count=1,
        train_numbers=["307"],
        notification_channel_ids=[],
        mode="real",
        reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
        status=WatchStatus.COMPLETED,
        dedupe_key="confirmed-paid-watch",
        created_at=now - timedelta(hours=1),
        updated_at=now - timedelta(minutes=2),
    )
    candidate = WatchCandidate(
        id=str(uuid.uuid4()),
        watch=watch,
        train_number="307",
        departure_at=now + timedelta(days=2),
        seat_class="first",
        priority=1,
        state="payment_required",
    )
    attempt = ReservationAttempt(
        candidate=candidate,
        idempotency_key="confirmed-paid-attempt",
        started_at=now - timedelta(minutes=20),
        finished_at=now - timedelta(minutes=19),
        outcome=ReservationOutcome.PAYMENT_REQUIRED,
        confirmation_outcome=ReservationConfirmationOutcome.CONFIRMED_PAID,
        confirmation_source="safe-paid-source",
        confirmation_observed_at=now - timedelta(hours=25),
        last_reconciled_at=now - timedelta(minutes=2),
    )
    raw_transition_reason = "reservation_reconciliation_confirmed_paid"
    transition = WatchTransitionHistory(
        watch=watch,
        from_status=WatchStatus.PAYMENT_REQUIRED,
        to_status=WatchStatus.COMPLETED,
        reason=raw_transition_reason,
        created_at=now - timedelta(minutes=2),
    )

    async with app.state.test_session_factory() as session:
        session.add_all([watch, candidate, attempt, transition])
        await session.commit()

    payload = (await client.get("/api/v1/operations/summary")).json()
    completed_entries = [
        entry for entry in payload["recent_entries"] if entry["reason_code"] == "payment_completed"
    ]

    assert len(completed_entries) == 1
    completed_entry = completed_entries[0]
    assert completed_entry["occurred_at"] == attempt.last_reconciled_at.isoformat().replace(
        "+00:00", "Z"
    )
    assert completed_entry == {
        "occurred_at": completed_entry["occurred_at"],
        "kind": "watch_transition",
        "level": "info",
        "status": "completed",
        "error_category": None,
        "provider": "srt",
        "train_number": "307",
        "departure_at": candidate.departure_at.astimezone(ZoneInfo("Asia/Seoul")).isoformat(),
        "seat_class": "first",
        "reason_code": "payment_completed",
    }
    assert raw_transition_reason not in str(payload)


async def test_operations_summary_does_not_invent_payment_completion_without_transition(
    app,
    client,
):
    now = datetime.now(UTC)
    watch = Watch(
        id=str(uuid.uuid4()),
        provider=Provider.SRT,
        origin="수서",
        destination="대전",
        travel_date=(now + timedelta(days=2)).date(),
        time_from=time(9),
        time_to=time(12),
        seat_class="standard",
        passenger_count=1,
        train_numbers=["309"],
        notification_channel_ids=[],
        mode="real",
        reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
        status=WatchStatus.EXPIRED,
        dedupe_key="confirmed-paid-without-transition",
        created_at=now - timedelta(hours=1),
        updated_at=now - timedelta(minutes=2),
    )
    candidate = WatchCandidate(
        id=str(uuid.uuid4()),
        watch=watch,
        train_number="309",
        departure_at=now + timedelta(days=2),
        seat_class="standard",
        priority=1,
        state="expired",
    )
    attempt = ReservationAttempt(
        candidate=candidate,
        idempotency_key="confirmed-paid-without-transition-attempt",
        started_at=now - timedelta(minutes=20),
        finished_at=now - timedelta(minutes=19),
        outcome=ReservationOutcome.PAYMENT_REQUIRED,
        confirmation_outcome=ReservationConfirmationOutcome.CONFIRMED_PAID,
        confirmation_source="safe-paid-source",
        confirmation_observed_at=now - timedelta(minutes=2),
        last_reconciled_at=now - timedelta(minutes=2),
    )

    async with app.state.test_session_factory() as session:
        session.add_all([watch, candidate, attempt])
        await session.commit()

    payload = (await client.get("/api/v1/operations/summary")).json()

    assert not any(
        entry["reason_code"] == "payment_completed" for entry in payload["recent_entries"]
    )


async def test_operations_summary_uses_null_rates_without_durable_outcomes(client):
    response = await client.get("/api/v1/operations/summary")
    assert response.status_code == 200
    payload = response.json()
    assert payload["seat_observation_error_rate"]["denominator"] == 0
    assert payload["seat_observation_error_rate"]["rate"] is None
    assert payload["notification_delivery_failure_rate"]["denominator"] == 0
    assert payload["notification_delivery_failure_rate"]["rate"] is None
    assert payload["recent_entries"] == []
    assert all(item["status"] == "unknown" for item in payload["source_freshness"])


async def test_operations_summary_rejects_far_future_freshness(app, client):
    future = datetime.now(UTC) + timedelta(minutes=10)
    async with app.state.test_session_factory() as session:
        session.add(
            StationCatalogCache(
                cache_key="tago_station_catalog_all",
                schema_version=2,
                station_count=0,
                retrieved_at=future,
                updated_at=future,
            )
        )
        await session.commit()

    payload = (await client.get("/api/v1/operations/summary")).json()
    catalog = next(
        item for item in payload["source_freshness"] if item["source"] == "station_catalog"
    )
    assert catalog["status"] == "unknown"
    assert catalog["age_seconds"] is None
    assert catalog["observed_at"] is not None
