from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, TextIO

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows unit tests use the thread lock.
    fcntl = None

DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 4
_SECRET_ENV_NAMES = (
    "AUTH_SESSION_SECRET",
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
    "DATABASE_URL",
    "DATABASE_PASSWORD",
    "KORAIL_BROWSER_ADAPTER_TOKEN",
    "POSTGRES_PASSWORD",
    "REDIS_URL",
    "SECRET_ENCRYPTION_KEY",
    "SRT_PROVIDER_ADAPTER_TOKEN",
    "TAGO_SERVICE_KEY",
    "WEBPUSH_VAPID_PRIVATE_KEY",
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|token|secret|authorization|cookie|api[_-]?key|service[_-]?key)"
    r"\s*[:=]\s*([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_URL_QUERY = re.compile(r"(https?://[^\s?]+)\?[^\s]+", re.IGNORECASE)
_RELATIVE_URL_QUERY = re.compile(r"(?P<path>/[^\s?\"]+)\?[^\s\"]+")
_URL_USERINFO = re.compile(r"([a-z][a-z0-9+.-]*://)[^/@\s]+@", re.IGNORECASE)
_handler_cache: dict[str, ServiceFileHandler] = {}
_handler_cache_lock = threading.Lock()


class _SafeConsoleHandler(logging.StreamHandler[TextIO]):
    """Marker type used to keep sidecar console configuration idempotent."""


def _bounded_integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer") from error
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def _sanitize(message: str) -> str:
    sanitized = message.replace("\r", "\\r").replace("\n", "\\n")
    for name in _SECRET_ENV_NAMES:
        value = os.getenv(name)
        if value and len(value) >= 4:
            sanitized = sanitized.replace(value, "[REDACTED]")
    sanitized = _BEARER.sub("Bearer [REDACTED]", sanitized)
    sanitized = _SENSITIVE_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", sanitized)
    sanitized = _URL_USERINFO.sub(r"\1[REDACTED]@", sanitized)
    sanitized = _URL_QUERY.sub(r"\1?[REDACTED]", sanitized)
    return _RELATIVE_URL_QUERY.sub(r"\g<path>?[REDACTED]", sanitized)


class SafeJsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, str] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "service": self._service,
            "level": record.levelname,
            "logger": record.name,
            "message": _sanitize(record.getMessage()),
        }
        if record.exc_info is not None and record.exc_info[0] is not None:
            payload["error_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class ServiceFileHandler(logging.Handler):
    """Cross-process-safe size rotation for one Compose service log file."""

    def __init__(
        self,
        filename: str | Path,
        *,
        service: str,
        max_bytes: int = DEFAULT_MAX_BYTES,
        backup_count: int = DEFAULT_BACKUP_COUNT,
    ) -> None:
        super().__init__()
        self.base_filename = Path(filename).resolve()
        self.lock_filename = self.base_filename.with_suffix(self.base_filename.suffix + ".lock")
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.base_filename.parent.mkdir(parents=True, exist_ok=True)
        self.setFormatter(SafeJsonFormatter(service))
        self._assert_writable()

    def _assert_writable(self) -> None:
        with self.base_filename.open("ab"):
            pass
        with self.lock_filename.open("ab"):
            pass

    @contextmanager
    def _process_lock(self) -> Iterator[BinaryIO]:
        with self.lock_filename.open("a+b") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield handle
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            encoded = (self.format(record) + "\n").encode("utf-8")
            with self._process_lock():
                current_size = self.base_filename.stat().st_size
                if current_size > 0 and current_size + len(encoded) > self.max_bytes:
                    self._rotate()
                with self.base_filename.open("ab") as stream:
                    stream.write(encoded)
                    stream.flush()
        except Exception:  # noqa: BLE001 - logging handlers must never crash the service.
            self.handleError(record)

    def _rotate(self) -> None:
        oldest = self.base_filename.with_name(f"{self.base_filename.name}.{self.backup_count}")
        oldest.unlink(missing_ok=True)
        for index in range(self.backup_count - 1, 0, -1):
            source = self.base_filename.with_name(f"{self.base_filename.name}.{index}")
            destination = self.base_filename.with_name(f"{self.base_filename.name}.{index + 1}")
            if source.exists():
                os.replace(source, destination)
        if self.base_filename.exists():
            os.replace(
                self.base_filename,
                self.base_filename.with_name(f"{self.base_filename.name}.1"),
            )


def _service_file_handler() -> ServiceFileHandler | None:
    filename = os.getenv("APP_LOG_FILE", "").strip()
    if not filename:
        return None
    service = os.getenv("APP_LOG_SERVICE", "backend").strip() or "backend"
    with _handler_cache_lock:
        cached = _handler_cache.get(filename)
        if cached is not None:
            return cached
        handler = ServiceFileHandler(
            filename,
            service=service,
            max_bytes=_bounded_integer(
                "APP_LOG_MAX_BYTES", DEFAULT_MAX_BYTES, 64 * 1024, 100 * 1024 * 1024
            ),
            backup_count=_bounded_integer("APP_LOG_BACKUP_COUNT", DEFAULT_BACKUP_COUNT, 1, 20),
        )
        handler.setLevel(os.getenv("APP_LOG_LEVEL", "INFO").upper())
        _handler_cache[filename] = handler
        return handler


def configure_service_file_logging(logger: logging.Logger | None = None) -> None:
    handler = _service_file_handler()
    if handler is None:
        return
    # Keep third-party loggers at their configured levels while ensuring every
    # application module below this namespace can emit the selected file-log level.
    # Setting only the handler level is insufficient because the root logger defaults
    # to WARNING and filters application INFO records before they reach the handler.
    logging.getLogger("rail_waitlist").setLevel(handler.level)
    targets = (
        [logger]
        if logger is not None
        else [
            logging.getLogger(),
            logging.getLogger("uvicorn"),
            logging.getLogger("uvicorn.access"),
        ]
    )
    for target in targets:
        if target is not None and handler not in target.handlers:
            target.addHandler(handler)


def configure_service_console_logging(
    logger: logging.Logger,
    *,
    stream: TextIO | None = None,
) -> None:
    """Emit sanitized application records to container stderr for sidecar diagnostics."""

    service = os.getenv("APP_LOG_SERVICE", "").strip()
    if not service:
        return
    level = os.getenv("APP_LOG_LEVEL", "INFO").upper()
    logger.setLevel(level)
    if any(isinstance(handler, _SafeConsoleHandler) for handler in logger.handlers):
        return
    handler = _SafeConsoleHandler(stream)
    handler.setLevel(level)
    handler.setFormatter(SafeJsonFormatter(service))
    logger.addHandler(handler)
