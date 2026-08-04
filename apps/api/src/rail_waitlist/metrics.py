from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter(
    "rail_waitlist_http_requests_total",
    "HTTP requests processed",
    ("method", "route", "status"),
)
HTTP_DURATION = Histogram(
    "rail_waitlist_http_request_duration_seconds",
    "HTTP request duration",
    ("method", "route"),
)
WORKER_RUNS = Counter(
    "rail_waitlist_worker_runs_total", "Celery task runs", ("task", "result")
)
WATCH_GROUPS = Counter(
    "rail_waitlist_watch_groups_processed_total", "Deduplicated watch groups processed"
)
PROVIDER_OPERATIONS = Counter(
    "rail_waitlist_provider_operations_total",
    "Normalized approved-provider operations without request or credential labels",
    ("provider", "operation", "result"),
)
PROVIDER_OPERATION_DURATION = Histogram(
    "rail_waitlist_provider_operation_duration_seconds",
    "Approved-provider operation duration without request or credential labels",
    ("provider", "operation"),
)
OUTBOX_DELIVERIES = Counter(
    "rail_waitlist_outbox_delivery_total", "Outbox notification delivery results", ("result",)
)
OUTBOX_PENDING = Gauge("rail_waitlist_outbox_pending", "Pending outbox events")
