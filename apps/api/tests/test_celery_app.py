from rail_waitlist.celery_app import celery_app


def test_due_watch_beat_uses_fast_expiring_schedule() -> None:
    schedule = celery_app.conf.beat_schedule["process-due-watches"]

    assert schedule["task"] == "rail_waitlist.worker.process_due_watches"
    assert schedule["schedule"] == 1.0
    assert schedule["options"] == {"expires": 1.0}
