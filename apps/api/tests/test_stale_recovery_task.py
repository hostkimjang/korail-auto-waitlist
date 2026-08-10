from __future__ import annotations

import rail_waitlist.worker as worker_module


def test_stale_recovery_runs_on_the_independent_maintenance_queue(
    monkeypatch,
) -> None:
    async def recover() -> int:
        return 7

    monkeypatch.setattr(worker_module, "_recover_stale_reservation_attempts_independently", recover)

    task = worker_module.recover_abandoned_reservations
    assert task.name == "rail_waitlist.worker.recover_stale_reservation_attempts"
    assert worker_module.celery_app.conf.task_routes[task.name] == {"queue": "maintenance"}
    assert worker_module.celery_app.conf.beat_schedule["recover-stale-reservation-attempts"] == {
        "task": task.name,
        "schedule": 30.0,
        "options": {"expires": 30.0},
    }
    assert task.run() == 7
