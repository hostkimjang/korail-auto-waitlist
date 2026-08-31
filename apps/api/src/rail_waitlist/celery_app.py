from __future__ import annotations

from celery import Celery
from celery.signals import after_setup_logger, after_setup_task_logger

from .config import get_settings
from .file_logging import configure_service_file_logging

configure_service_file_logging()


@after_setup_logger.connect
@after_setup_task_logger.connect
def _configure_celery_file_logging(logger, **_kwargs) -> None:
    configure_service_file_logging(logger)


settings = get_settings()
celery_app = Celery(
    "rail_waitlist",
    broker=settings.celery_broker_url or settings.redis_url,
    backend=settings.celery_result_backend or settings.redis_url,
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Seoul",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "rail_waitlist.worker.process_due_watches": {"queue": "rail"},
        "rail_waitlist.worker.process_watch_now": {"queue": "rail"},
        "rail_waitlist.worker.reconcile_reservation_attempt": {"queue": "rail"},
        "rail_waitlist.worker.expire_elapsed_watches": {"queue": "maintenance"},
        "rail_waitlist.worker.recover_stale_reservation_attempts": {"queue": "maintenance"},
        "rail_waitlist.worker.deliver_outbox": {"queue": "notifications"},
    },
    beat_schedule={
        "process-due-watches": {
            "task": "rail_waitlist.worker.process_due_watches",
            "schedule": 1.0,
            "options": {"expires": 1.0},
        },
        "deliver-outbox": {
            "task": "rail_waitlist.worker.deliver_outbox",
            "schedule": 5.0,
        },
        "recover-stale-reservation-attempts": {
            "task": "rail_waitlist.worker.recover_stale_reservation_attempts",
            "schedule": 30.0,
            "options": {"expires": 30.0},
        },
        "expire-elapsed-watches": {
            "task": "rail_waitlist.worker.expire_elapsed_watches",
            "schedule": 30.0,
            "options": {"expires": 30.0},
        },
    },
)
