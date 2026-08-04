from __future__ import annotations

import uuid
from datetime import UTC, datetime, time, timedelta

from rail_waitlist.domain import (
    OutboxStatus,
    Provider,
    ProviderCircuitState,
    ReservationOutcome,
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
from rail_waitlist.schemas import OperationsSummary as CompatibilityOperationsSummary


def test_operations_summary_schema_keeps_compatibility_export():
    assert CompatibilityOperationsSummary is FeatureOperationsSummary


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
        train_numbers=["PRIVATE-TRAIN-77"],
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
        train_number="PRIVATE-TRAIN-77",
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

    serialized = response.text
    for forbidden in (
        secret_marker,
        watch_id,
        candidate_id,
        "PRIVATE-TRAIN-77",
        "비공개 출발역",
        "비공개 도착역",
        "private-channel",
        "request_body",
        "last_error",
        "reason",
        "official_handoff_url",
    ):
        assert forbidden not in serialized


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
