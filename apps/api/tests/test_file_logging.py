import json
import logging
from io import StringIO

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
