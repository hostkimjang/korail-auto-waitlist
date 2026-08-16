import json
import logging
from io import StringIO

import httpx
import pytest

from rail_waitlist.file_logging import (
    ServiceFileHandler,
    configure_service_console_logging,
    configure_service_file_logging,
)


def test_service_file_handler_redacts_secrets_and_query_strings(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("KORAIL_BROWSER_ADAPTER_TOKEN", "adapter-secret-sentinel")
    monkeypatch.setenv("SRT_PROVIDER_ADAPTER_TOKEN", "srt-adapter-secret-sentinel")
    output = tmp_path / "api.log"
    logger = logging.getLogger("test.file_logging.redaction")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    handler = ServiceFileHandler(output, service="api", max_bytes=4096, backup_count=2)
    logger.addHandler(handler)

    logger.info(
        "Bearer visible-token token=visible-token configured=%s url=%s request=%s",
        "adapter-secret-sentinel srt-adapter-secret-sentinel",
        "https://example.invalid/path?credential=visible-token",
        "/api/v1/timetables?origin=Seoul&destination=Busan",
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    serialized = json.dumps(payload, ensure_ascii=False)
    assert payload["service"] == "api"
    assert payload["level"] == "INFO"
    assert "adapter-secret-sentinel" not in serialized
    assert "srt-adapter-secret-sentinel" not in serialized
    assert "visible-token" not in serialized
    assert "Bearer [REDACTED]" in payload["message"]
    assert "https://example.invalid/path?[REDACTED]" in payload["message"]
    assert "/api/v1/timetables?[REDACTED]" in payload["message"]


def test_service_file_handler_redacts_known_webhook_path_credentials(tmp_path) -> None:
    output = tmp_path / "notification-worker.log"
    logger = logging.getLogger("test.file_logging.webhook_paths")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    handler = ServiceFileHandler(output, service="notification-worker", max_bytes=4096)
    logger.addHandler(handler)
    telegram_secret = "123456789:telegramSyntheticCredential1234567890"
    discord_id = "123456789012345678"
    discord_secret = "discordSyntheticCredential_12345678901234567890"
    slack_team = "T12345678"
    slack_channel = "B12345678"
    slack_secret = "slackSyntheticCredential1234567890"
    public_url = "https://example.invalid/public/releases/2026-08-14"

    logger.info(
        "delivery telegram=%s discord=%s slack=%s public=%s",
        f"https://api.telegram.invalid/bot{telegram_secret}/sendMessage",
        f"https://203.0.113.10/api/webhooks/{discord_id}/{discord_secret}",
        f"https://hooks.example.invalid/services/{slack_team}/{slack_channel}/{slack_secret}",
        public_url,
    )

    message = json.loads(output.read_text(encoding="utf-8"))["message"]
    assert telegram_secret not in message
    assert discord_id not in message
    assert discord_secret not in message
    assert slack_team not in message
    assert slack_channel not in message
    assert slack_secret not in message
    assert "/bot[REDACTED]/sendMessage" in message
    assert "/api/webhooks/[REDACTED]/[REDACTED]" in message
    assert "/services/[REDACTED]/[REDACTED]/[REDACTED]" in message
    assert public_url in message


def test_service_file_handler_rotates_with_a_fixed_backup_limit(tmp_path) -> None:
    output = tmp_path / "worker.log"
    logger = logging.getLogger("test.file_logging.rotation")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    handler = ServiceFileHandler(output, service="worker", max_bytes=256, backup_count=2)
    logger.addHandler(handler)

    for index in range(12):
        logger.info("rotation-event index=%s payload=%s", index, "x" * 80)

    assert output.exists()
    assert (tmp_path / "worker.log.1").exists()
    assert (tmp_path / "worker.log.2").exists()
    assert not (tmp_path / "worker.log.3").exists()


def test_configure_service_file_logging_enables_application_info_events(
    tmp_path,
    monkeypatch,
) -> None:
    output = tmp_path / "sidecar.log"
    monkeypatch.setenv("APP_LOG_FILE", str(output))
    monkeypatch.setenv("APP_LOG_SERVICE", "korail-browser-adapter")
    monkeypatch.setenv("APP_LOG_LEVEL", "INFO")
    package_logger = logging.getLogger("rail_waitlist")
    lifecycle_logger = logging.getLogger("rail_waitlist.korail_pydoll_browser")
    previous_package_level = package_logger.level
    previous_lifecycle_level = lifecycle_logger.level
    package_logger.handlers.clear()
    lifecycle_logger.setLevel(logging.NOTSET)

    try:
        configure_service_file_logging(package_logger)
        lifecycle_logger.info("KORAIL HTTP replay event=search_succeeded lease_search_index=2")
    finally:
        package_logger.setLevel(previous_package_level)
        lifecycle_logger.setLevel(previous_lifecycle_level)
        package_logger.handlers.clear()

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["service"] == "korail-browser-adapter"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "rail_waitlist.korail_pydoll_browser"
    assert "event=search_succeeded" in payload["message"]
    assert payload["event"] == "search_succeeded"


def test_service_file_handler_promotes_only_valid_correlation_fields(tmp_path) -> None:
    output = tmp_path / "correlation.log"
    logger = logging.getLogger("test.file_logging.correlation")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    handler = ServiceFileHandler(output, service="worker", max_bytes=4096, backup_count=2)
    logger.addHandler(handler)

    request_id = "550e8400e29b41d4a716446655440000"
    provider_call_id = "2f1c43d91be94a89a7bf97789c62d52f"
    logger.info(
        "조회 대기 event=provider_call_joined request_id=%s provider_call_id=%s",
        request_id,
        provider_call_id,
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["event"] == "provider_call_joined"
    assert payload["request_id"] == request_id
    assert payload["provider_call_id"] == provider_call_id

    logger.info(
        "거절 입력 event=BAD request_id=not-a-uuid provider_call_id=%s",
        "f" * 33,
    )
    rejected = json.loads(output.read_text(encoding="utf-8").splitlines()[1])
    assert "event" not in rejected
    assert "request_id" not in rejected
    assert "provider_call_id" not in rejected


def test_configure_service_console_logging_is_sanitized_and_idempotent(monkeypatch) -> None:
    monkeypatch.setenv("APP_LOG_SERVICE", "srt-provider-adapter")
    monkeypatch.setenv("APP_LOG_LEVEL", "INFO")
    monkeypatch.setenv("SRT_PROVIDER_ADAPTER_TOKEN", "srt-console-secret")
    stream = StringIO()
    logger = logging.getLogger("test.file_logging.console")
    previous_level = logger.level
    previous_propagate = logger.propagate
    logger.handlers.clear()
    logger.propagate = False

    try:
        configure_service_console_logging(logger, stream=stream)
        configure_service_console_logging(logger, stream=stream)
        logger.info(
            "SRT queue released token=visible-token configured=%s",
            "srt-console-secret",
        )
    finally:
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate
        logger.handlers.clear()

    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["service"] == "srt-provider-adapter"
    assert payload["level"] == "INFO"
    assert "srt-console-secret" not in payload["message"]
    assert "visible-token" not in payload["message"]
    assert "token=[REDACTED]" in payload["message"]


@pytest.mark.parametrize("logger_name", ["httpx", "httpcore.connection"])
def test_transport_records_are_excluded_while_application_logs_are_preserved(
    tmp_path,
    monkeypatch,
    logger_name: str,
) -> None:
    output = tmp_path / f"{logger_name}.log"
    monkeypatch.setenv("APP_LOG_FILE", str(output))
    monkeypatch.setenv("APP_LOG_SERVICE", "notification-worker")
    monkeypatch.setenv("APP_LOG_LEVEL", "INFO")
    stream = StringIO()
    celery_console_stream = StringIO()
    logger = logging.getLogger(logger_name)
    application_logger = logging.getLogger(f"rail_waitlist.test.file_logging.{logger_name}")
    package_logger = logging.getLogger("rail_waitlist")
    previous_handlers = list(logger.handlers)
    previous_level = logger.level
    previous_disabled = logger.disabled
    previous_propagate = logger.propagate
    previous_application_handlers = list(application_logger.handlers)
    previous_application_level = application_logger.level
    previous_application_disabled = application_logger.disabled
    previous_application_propagate = application_logger.propagate
    previous_package_level = package_logger.level
    logger.handlers.clear()
    logger.disabled = False
    logger.propagate = False
    logger.setLevel(logging.INFO)
    application_logger.handlers.clear()
    application_logger.disabled = False
    application_logger.propagate = False
    application_logger.setLevel(logging.INFO)
    celery_console = logging.StreamHandler(celery_console_stream)
    logger.addHandler(celery_console)
    synthetic_path_credential = "syntheticPathCredential1234567890"
    sensitive_url = f"https://example.invalid/hooks/{synthetic_path_credential}"

    def emit_transport_info() -> None:
        if logger_name == "httpx":
            transport = httpx.MockTransport(
                lambda request: httpx.Response(204, request=request),
            )
            with httpx.Client(transport=transport) as client:
                client.post(sensitive_url)
        else:
            logger.info(
                'HTTP Request: POST "%s" "HTTP/1.1 204"',
                sensitive_url,
            )

    try:
        configure_service_file_logging(logger)
        configure_service_console_logging(logger, stream=stream)
        assert logger.level == logging.WARNING
        for handler in logger.handlers:
            application_logger.addHandler(handler)
        emit_transport_info()
        assert celery_console_stream.getvalue() == ""

        # A later logging reconfiguration must not reopen either managed sink.
        logger.setLevel(logging.DEBUG)
        emit_transport_info()
        logger.warning("transport warning target=%s", sensitive_url)
        logger.error("transport error target=%s", sensitive_url)
        application_logger.info("application delivery summary")
        application_logger.warning("application delivery warning")
    finally:
        logger.handlers.clear()
        logger.handlers.extend(previous_handlers)
        logger.setLevel(previous_level)
        logger.disabled = previous_disabled
        logger.propagate = previous_propagate
        application_logger.handlers.clear()
        application_logger.handlers.extend(previous_application_handlers)
        application_logger.setLevel(previous_application_level)
        application_logger.disabled = previous_application_disabled
        application_logger.propagate = previous_application_propagate
        package_logger.setLevel(previous_package_level)

    file_payloads = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    console_payloads = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [payload["message"] for payload in file_payloads] == [
        "application delivery summary",
        "application delivery warning",
    ]
    assert [payload["message"] for payload in console_payloads] == [
        "application delivery summary",
        "application delivery warning",
    ]
    assert celery_console_stream.getvalue().splitlines() == [
        "application delivery summary",
        "application delivery warning",
    ]
    assert synthetic_path_credential not in output.read_text(encoding="utf-8")
    assert synthetic_path_credential not in stream.getvalue()
    assert synthetic_path_credential not in celery_console_stream.getvalue()
