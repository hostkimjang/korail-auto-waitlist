from datetime import datetime, timezone

from rail_waitlist.domain import ReservationOutcome, ReservationPolicy
from rail_waitlist.models import ReservationAttempt
from rail_waitlist.reservation_confirmation import ReservationConfirmationOutcome
from rail_waitlist.watch_management import http as watch_http
from rail_waitlist.watch_management.read_model import reservation_attempt_projection

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


def test_watch_management_router_owns_exact_existing_routes() -> None:
    assert _route_contract(watch_http.router) == WATCH_ROUTES


async def test_watch_management_routes_require_admin_session(public_client) -> None:
    response = await public_client.get("/api/v1/watches")

    assert response.status_code == 401


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
    now = datetime.now(timezone.utc)
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
