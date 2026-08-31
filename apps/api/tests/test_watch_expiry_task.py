from __future__ import annotations

import rail_waitlist.worker as worker_module


def test_watch_expiry_runs_on_the_independent_maintenance_queue(
    monkeypatch,
) -> None:
    async def expire() -> int:
        return 5

    monkeypatch.setattr(worker_module, "_expire_elapsed_watches_independently", expire)

    task = worker_module.expire_watches_maintenance
    assert task.name == "rail_waitlist.worker.expire_elapsed_watches"
    assert worker_module.celery_app.conf.task_routes[task.name] == {"queue": "maintenance"}
    assert worker_module.celery_app.conf.beat_schedule["expire-elapsed-watches"] == {
        "task": task.name,
        "schedule": 30.0,
        "options": {"expires": 30.0},
    }
    assert task.run() == 5
