from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select

from rail_waitlist.domain import ReservationOutcome, ReservationPolicy, WatchStatus
from rail_waitlist.reservation_confirmation import ReservationConfirmationOutcome
from rail_waitlist.reservations.manual_rearm_contracts import ManualReservationRearmReason
from rail_waitlist.reservations.reconciliation_policy import (
    ReservationReconciliationResolution,
)
from rail_waitlist.watch_management import http as watch_http
from rail_waitlist.watch_management.models import ReservationAttempt, Watch, WatchCandidate
from rail_waitlist.watch_management.read_model import reservation_attempt_projection
from rail_waitlist.watch_management.schemas import ManualReservationRearmRequest

WATCH_ROUTES = {
    ("POST", "/api/v1/watches"),
    ("GET", "/api/v1/watches"),
    ("GET", "/api/v1/watches/{watch_id}"),
    ("PATCH", "/api/v1/watches/{watch_id}"),
    ("DELETE", "/api/v1/watches/{watch_id}"),
    ("POST", "/api/v1/watches/{watch_id}/start"),
    ("POST", "/api/v1/watches/{watch_id}/pause"),
    ("POST", "/api/v1/watches/{watch_id}/cancel"),
    ("POST", "/api/v1/watches/{watch_id}/reservation-rearm"),
    ("POST", "/api/v1/watches/{watch_id}/mock-transition"),
}


def _route_contract(router) -> set[tuple[str, str]]:
    return {
        (method, route.path)
        for route in router.routes
        for method in (route.methods or set())
        if method in {"GET", "POST", "PATCH", "DELETE"}
    }


def test_manual_rearm_request_requires_a_true_generic_official_state_ack() -> None:
    request = ManualReservationRearmRequest.model_validate(
        {
            "reason": "unknown_result_unresolved",
            "official_reservation_state_confirmed": True,
        }
    )

    assert request.reason is not None
    assert request.reason.value == "unknown_result_unresolved"
    assert request.official_reservation_state_confirmed is True
    assert ManualReservationRearmRequest.model_validate({}).reason is None
    with pytest.raises(ValidationError):
        ManualReservationRearmRequest.model_validate(
            {
                "reason": "unknown_result_unresolved",
                "official_reservation_state_confirmed": False,
            }
        )


def _watch_payload(train_number: str) -> dict[str, object]:
    travel_date = (datetime.now(UTC).date() + timedelta(days=7)).isoformat()
    return {
        "provider": "mock",
        "origin": "서울",
        "origin_node_id": "N-SEOUL",
        "destination": "부산",
        "destination_node_id": "N-BUSAN",
        "travel_date": travel_date,
        "time_from": "08:00:00",
        "time_to": "12:00:00",
        "passenger_count": 1,
        "train_numbers": [train_number],
        "mode": "official",
        "candidates": [
            {
                "train_number": train_number,
                "departure_at": f"{travel_date}T08:30:00+09:00",
                "arrival_at": f"{travel_date}T11:00:00+09:00",
                "seat_class": "standard",
                "priority": 1,
            }
        ],
    }


async def _create_watch(client: AsyncClient, train_number: str) -> str:
    response = await client.post("/api/v1/watches", json=_watch_payload(train_number))
    assert response.status_code == 201
    payload = response.json()
    assert isinstance(payload, dict)
    watch_id = payload.get("id")
    assert isinstance(watch_id, str)
    return watch_id


def test_watch_management_router_owns_exact_existing_routes() -> None:
    assert _route_contract(watch_http.router) == WATCH_ROUTES


async def test_watch_management_routes_require_admin_session(public_client) -> None:
    response = await public_client.get("/api/v1/watches")

    assert response.status_code == 401


async def test_live_watch_view_filters_history_before_projection_and_preserves_manual_checks(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    watch_ids = {
        name: await _create_watch(client, train_number)
        for name, train_number in (
            ("active", "KTX-LIVE-ACTIVE"),
            ("payment", "KTX-LIVE-PAYMENT"),
            ("expired", "KTX-HISTORY-EXPIRED"),
            ("recent", "KTX-LIVE-RECENT"),
            ("manual", "KTX-LIVE-MANUAL"),
            ("superseded", "KTX-HISTORY-SUPERSEDED"),
            ("completed", "KTX-LIVE-COMPLETED"),
            ("paid_unknown", "KTX-HISTORY-PAID-UNKNOWN"),
            ("failed", "KTX-LIVE-FAILED"),
            ("confirmed_absent", "KTX-HISTORY-CONFIRMED-ABSENT"),
            ("exhausted", "KTX-LIVE-EXHAUSTED"),
        )
    }
    now = datetime.now(UTC)
    async with app.state.test_session_factory() as session:
        watches: dict[str, Watch] = {}
        candidates: dict[str, WatchCandidate] = {}
        for name, watch_id in watch_ids.items():
            watch = await session.get(Watch, watch_id)
            candidate = await session.scalar(
                select(WatchCandidate).where(WatchCandidate.watch_id == watch_id)
            )
            assert watch is not None
            assert candidate is not None
            watches[name] = watch
            candidates[name] = candidate

        watches["payment"].status = WatchStatus.PAYMENT_REQUIRED
        watches["expired"].status = WatchStatus.EXPIRED
        watches["recent"].status = WatchStatus.EXPIRED
        watches["manual"].status = WatchStatus.EXPIRED
        watches["superseded"].status = WatchStatus.EXPIRED
        watches["completed"].status = WatchStatus.COMPLETED
        watches["paid_unknown"].status = WatchStatus.PAUSED
        watches["failed"].status = WatchStatus.FAILED
        watches["confirmed_absent"].status = WatchStatus.EXPIRED
        watches["exhausted"].status = WatchStatus.EXPIRED

        candidates["payment"].state = "payment_required"
        for name in (
            "expired",
            "recent",
            "manual",
            "superseded",
            "confirmed_absent",
            "exhausted",
        ):
            candidates[name].state = "expired"
        candidates["completed"].state = "payment_required"
        candidates["paid_unknown"].state = "expired"
        candidates["failed"].state = "failed"
        for name in (
            "expired",
            "manual",
            "superseded",
            "paid_unknown",
            "failed",
            "confirmed_absent",
            "exhausted",
        ):
            watches[name].updated_at = now - timedelta(days=2)
        paid_unknown_latest_candidate = WatchCandidate(
            watch=watches["paid_unknown"],
            train_number="KTX-HISTORY-PAID-UNKNOWN-LATEST",
            departure_at=now + timedelta(days=7, minutes=1),
            scheduled_departure_at=now + timedelta(days=7, minutes=1),
            seat_class="standard",
            priority=2,
            state="expired",
        )

        session.add_all(
            [
                ReservationAttempt(
                    candidate_id=candidates["manual"].id,
                    attempt_sequence=1,
                    episode_key="manual-check-episode",
                    idempotency_key="manual-check-attempt",
                    outcome=ReservationOutcome.UNKNOWN,
                    started_at=now,
                    finished_at=now,
                ),
                ReservationAttempt(
                    candidate_id=candidates["completed"].id,
                    attempt_sequence=1,
                    episode_key="completed-payment-episode",
                    idempotency_key="completed-payment-attempt",
                    outcome=ReservationOutcome.PAYMENT_REQUIRED,
                    started_at=now,
                    finished_at=now,
                ),
                ReservationAttempt(
                    candidate_id=candidates["paid_unknown"].id,
                    attempt_sequence=1,
                    episode_key="paid-unknown-episode",
                    idempotency_key="paid-unknown-attempt",
                    outcome=ReservationOutcome.UNKNOWN,
                    started_at=now - timedelta(days=2),
                    finished_at=now - timedelta(days=2),
                ),
                ReservationAttempt(
                    candidate=paid_unknown_latest_candidate,
                    attempt_sequence=1,
                    episode_key="paid-unknown-latest-episode",
                    idempotency_key="paid-unknown-latest-attempt",
                    outcome=ReservationOutcome.UNKNOWN,
                    confirmation_outcome=ReservationConfirmationOutcome.CONFIRMED_PAID,
                    confirmation_source="official-reservation-list",
                    confirmation_observed_at=now - timedelta(days=2),
                    last_reconciled_at=now - timedelta(days=2),
                    reconciliation_attempt_count=1,
                    started_at=now - timedelta(days=2, minutes=2),
                    finished_at=now - timedelta(days=2, minutes=2),
                ),
                ReservationAttempt(
                    candidate_id=candidates["superseded"].id,
                    attempt_sequence=1,
                    episode_key="superseded-manual-episode",
                    idempotency_key="superseded-manual-attempt",
                    outcome=ReservationOutcome.UNKNOWN,
                    started_at=now - timedelta(minutes=2),
                    finished_at=now - timedelta(minutes=2),
                ),
                ReservationAttempt(
                    candidate_id=candidates["superseded"].id,
                    attempt_sequence=2,
                    episode_key="superseding-payment-episode",
                    idempotency_key="superseding-payment-attempt",
                    outcome=ReservationOutcome.PAYMENT_REQUIRED,
                    started_at=now - timedelta(minutes=1),
                    finished_at=now - timedelta(minutes=1),
                ),
                ReservationAttempt(
                    candidate_id=candidates["failed"].id,
                    attempt_sequence=1,
                    episode_key="failed-attempt-episode",
                    idempotency_key="failed-attempt",
                    outcome=ReservationOutcome.FAILED,
                    started_at=now,
                    finished_at=now,
                ),
                ReservationAttempt(
                    candidate_id=candidates["confirmed_absent"].id,
                    attempt_sequence=1,
                    episode_key="confirmed-absent-source-episode",
                    idempotency_key="confirmed-absent-source-attempt",
                    outcome=ReservationOutcome.UNKNOWN,
                    confirmation_outcome=ReservationConfirmationOutcome.NOT_FOUND,
                    confirmation_source="official-reservation-list",
                    confirmation_observed_at=now - timedelta(days=2),
                    last_reconciled_at=now - timedelta(days=2),
                    reconciliation_attempt_count=2,
                    reconciliation_resolution=(
                        ReservationReconciliationResolution.CONFIRMED_ABSENT
                    ),
                    started_at=now - timedelta(days=2, minutes=2),
                    finished_at=now - timedelta(days=2, minutes=1),
                ),
                ReservationAttempt(
                    candidate_id=candidates["exhausted"].id,
                    attempt_sequence=1,
                    episode_key="exhausted-unresolved-episode",
                    idempotency_key="exhausted-unresolved-attempt",
                    outcome=ReservationOutcome.UNKNOWN,
                    confirmation_outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
                    confirmation_source="official-reservation-list",
                    confirmation_observed_at=now - timedelta(days=2),
                    last_reconciled_at=now - timedelta(days=2),
                    reconciliation_attempt_count=6,
                    reconciliation_resolution=(
                        ReservationReconciliationResolution.EXHAUSTED_UNRESOLVED
                    ),
                    started_at=now - timedelta(days=2, minutes=2),
                    finished_at=now - timedelta(days=2, minutes=1),
                ),
            ]
        )
        await session.commit()

    full_history = await client.get("/api/v1/watches")
    live = await client.get("/api/v1/watches", params={"view": "live"})
    expired_history = await client.get("/api/v1/watches", params={"status": "expired"})
    live_expired = await client.get(
        "/api/v1/watches",
        params={"view": "live", "status": "expired"},
    )

    assert full_history.status_code == 200
    assert {watch["id"] for watch in full_history.json()} == set(watch_ids.values())
    assert live.status_code == 200
    assert {watch["id"] for watch in live.json()} == {
        watch_ids["active"],
        watch_ids["payment"],
        watch_ids["recent"],
        watch_ids["manual"],
        watch_ids["completed"],
        watch_ids["exhausted"],
    }
    manual_watch = next(watch for watch in live.json() if watch["id"] == watch_ids["manual"])
    assert (
        manual_watch["candidates"][0]["latest_reservation_attempt"]["manual_check_required"] is True
    )
    paid_unknown_watch = next(
        watch for watch in full_history.json() if watch["id"] == watch_ids["paid_unknown"]
    )
    paid_unknown_attempt = next(
        candidate["latest_reservation_attempt"]
        for candidate in paid_unknown_watch["candidates"]
        if candidate["latest_reservation_attempt"]["confirmation_outcome"] == "confirmed_paid"
    )
    old_unknown_attempt = next(
        candidate["latest_reservation_attempt"]
        for candidate in paid_unknown_watch["candidates"]
        if candidate["latest_reservation_attempt"]["confirmation_outcome"] is None
    )
    assert old_unknown_attempt["manual_check_required"] is True
    assert paid_unknown_attempt["outcome"] == "unknown"
    assert paid_unknown_attempt["confirmation_outcome"] == "confirmed_paid"
    assert paid_unknown_attempt["manual_check_required"] is False
    assert paid_unknown_attempt["manual_rearm_available"] is False
    assert paid_unknown_attempt["manual_rearm_reason"] is None
    confirmed_absent_watch = next(
        watch for watch in full_history.json() if watch["id"] == watch_ids["confirmed_absent"]
    )
    exhausted_watch = next(watch for watch in live.json() if watch["id"] == watch_ids["exhausted"])
    assert (
        confirmed_absent_watch["candidates"][0]["latest_reservation_attempt"][
            "manual_check_required"
        ]
        is False
    )
    assert (
        exhausted_watch["candidates"][0]["latest_reservation_attempt"]["manual_check_required"]
        is True
    )
    assert expired_history.status_code == 200
    assert {watch["id"] for watch in expired_history.json()} == {
        watch_ids["expired"],
        watch_ids["recent"],
        watch_ids["manual"],
        watch_ids["superseded"],
        watch_ids["confirmed_absent"],
        watch_ids["exhausted"],
    }
    assert live_expired.status_code == 200
    assert {watch["id"] for watch in live_expired.json()} == {
        watch_ids["recent"],
        watch_ids["manual"],
        watch_ids["exhausted"],
    }


@pytest.mark.parametrize(
    "params",
    (
        {"view": "recent"},
        {"status": "not-a-watch-status"},
    ),
    ids=("invalid-view", "invalid-status"),
)
async def test_watch_list_rejects_invalid_query_values(
    client: AsyncClient,
    params: dict[str, str],
) -> None:
    response = await client.get("/api/v1/watches", params=params)

    assert response.status_code == 422


def test_immediate_processing_uses_exact_durable_task_and_is_best_effort(monkeypatch) -> None:
    calls: list[tuple[str, list[str], str]] = []

    def send_task(task_name: str, *, args: list[str], queue: str) -> None:
        calls.append((task_name, args, queue))

    monkeypatch.setattr(watch_http.celery_app, "send_task", send_task)

    assert watch_http.enqueue_immediate_watch_processing("watch-1") is True
    assert calls == [("rail_waitlist.worker.process_watch_now", ["watch-1"], "rail")]

    def unavailable_broker(*_args, **_kwargs) -> None:
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(watch_http.celery_app, "send_task", unavailable_broker)
    assert watch_http.enqueue_immediate_watch_processing("watch-2") is False


def test_payment_hold_projection_preserves_policy_specific_retry_boundary() -> None:
    now = datetime.now(UTC)
    attempt = ReservationAttempt(
        candidate_id="candidate-1",
        idempotency_key="reservation-attempt-1",
        outcome=ReservationOutcome.PAYMENT_REQUIRED,
        confirmation_outcome=ReservationConfirmationOutcome.NOT_FOUND,
        confirmation_source="official-reservation-list",
        confirmation_observed_at=now,
        post_deadline_reconciled_at=now,
        started_at=now,
        finished_at=now,
    )

    notify_only = reservation_attempt_projection(ReservationPolicy.NOTIFY_ONLY, attempt)
    automatic = reservation_attempt_projection(
        ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
        attempt,
    )

    assert notify_only["payment_hold_end_reason"] == "confirmed_payment_hold_no_longer_present"
    assert notify_only["retryable"] is False
    assert notify_only["manual_check_required"] is False
    assert notify_only["retry_condition"] is None
    assert automatic["retryable"] is True
    assert automatic["manual_check_required"] is False
    assert automatic["retry_condition"] == "new_availability_episode"


def test_confirmed_paid_unknown_projection_closes_manual_check_and_rearm() -> None:
    now = datetime.now(UTC)
    attempt = ReservationAttempt(
        candidate_id="candidate-1",
        idempotency_key="confirmed-paid-unknown-attempt",
        outcome=ReservationOutcome.UNKNOWN,
        confirmation_outcome=ReservationConfirmationOutcome.CONFIRMED_PAID,
        confirmation_source="official-reservation-list",
        confirmation_observed_at=now,
        last_reconciled_at=now,
        reconciliation_attempt_count=1,
        started_at=now - timedelta(seconds=2),
        finished_at=now - timedelta(seconds=1),
    )

    projection = reservation_attempt_projection(
        ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
        attempt,
        manual_rearm_reason=ManualReservationRearmReason.UNKNOWN_RESULT_UNRESOLVED,
    )

    assert projection["outcome"] is ReservationOutcome.UNKNOWN
    assert projection["confirmation_outcome"] is ReservationConfirmationOutcome.CONFIRMED_PAID
    assert projection["manual_check_required"] is False
    assert projection["manual_rearm_available"] is False
    assert projection["manual_rearm_reason"] is None
