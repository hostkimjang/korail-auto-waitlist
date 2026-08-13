from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from ..provider_call_context import new_log_id, validated_log_id

READ_ONLY_CALL_ID_HEADER = "X-Rail-Read-Only-Call-ID"

ReadOnlyCallState = Literal["pending", "terminal", "unknown"]


class ReadOnlyCallSource(Protocol):
    async def read_only_call_pending(self, request_id: str) -> bool: ...


@dataclass
class _ReadOnlyCallRecord:
    request_id: str
    phase: Literal["registered", "active", "awaiting_provider", "terminal"]
    expires_at: float


class SrtReadOnlyCallRegistry:
    """Track one sidecar HTTP call until its linked provider work is terminal."""

    def __init__(
        self,
        source: ReadOnlyCallSource,
        *,
        registration_grace_seconds: float = 60.0,
        terminal_tombstone_seconds: float = 300.0,
        max_terminal_tombstones: int = 4096,
        monotonic: Callable[[], float] = time.monotonic,
        instance_id: str | None = None,
    ) -> None:
        if (
            registration_grace_seconds <= 0
            or terminal_tombstone_seconds <= 0
            or max_terminal_tombstones <= 0
        ):
            raise ValueError("read-only lifecycle limits must be positive")
        self._source = source
        self._registration_grace_seconds = registration_grace_seconds
        self._terminal_tombstone_seconds = terminal_tombstone_seconds
        self._max_terminal_tombstones = max_terminal_tombstones
        self._monotonic = monotonic
        self._instance_id = instance_id or new_log_id()
        self._records: dict[str, _ReadOnlyCallRecord] = {}
        self._lock = asyncio.Lock()

    @property
    def instance_id(self) -> str:
        return self._instance_id

    async def register(self, call_id: str, request_id: str) -> bool:
        self._require_log_id(call_id, label="call_id")
        self._require_log_id(request_id, label="request_id")
        now = self._monotonic()
        async with self._lock:
            self._prune_records(now)
            record = self._records.get(call_id)
            if record is None:
                self._records[call_id] = _ReadOnlyCallRecord(
                    request_id=request_id,
                    phase="registered",
                    expires_at=now + self._registration_grace_seconds,
                )
                return True
            if record.phase == "registered" and record.request_id == request_id:
                record.expires_at = now + self._registration_grace_seconds
                return True
            return False

    async def begin(self, call_id: str, request_id: str) -> bool:
        if validated_log_id(call_id) is None or validated_log_id(request_id) is None:
            return False
        now = self._monotonic()
        async with self._lock:
            self._prune_records(now)
            record = self._records.get(call_id)
            if record is None or record.request_id != request_id:
                return False
            if record.phase != "registered":
                return False
            if record.expires_at <= now:
                self._mark_terminal(record, now)
                return False
            record.phase = "active"
            record.expires_at = float("inf")
            return True

    async def finish(self, call_id: str) -> None:
        now = self._monotonic()
        async with self._lock:
            record = self._records.get(call_id)
            if record is not None and record.phase == "active":
                record.phase = "awaiting_provider"
                record.expires_at = float("inf")
            self._prune_records(now)

    async def status(self, call_id: str) -> ReadOnlyCallState:
        if validated_log_id(call_id) is None:
            return "unknown"
        now = self._monotonic()
        async with self._lock:
            self._prune_records(now)
            record = self._records.get(call_id)
            if record is None:
                return "unknown"
            if record.phase == "registered":
                if record.expires_at <= now:
                    self._mark_terminal(record, now)
                    return "terminal"
                return "pending"
            if record.phase == "active":
                return "pending"
            if record.phase == "terminal":
                return "terminal"
            request_id = record.request_id

        if await self._source.read_only_call_pending(request_id):
            return "pending"

        now = self._monotonic()
        async with self._lock:
            record = self._records.get(call_id)
            if record is None:
                return "unknown"
            if record.phase == "awaiting_provider":
                self._mark_terminal(record, now)
                self._prune_records(now)
                return "terminal"
            return "terminal" if record.phase == "terminal" else "pending"

    def _mark_terminal(self, record: _ReadOnlyCallRecord, now: float) -> None:
        record.phase = "terminal"
        record.expires_at = now + self._terminal_tombstone_seconds

    def _prune_records(self, now: float) -> None:
        for record in self._records.values():
            if record.phase == "registered" and record.expires_at <= now:
                self._mark_terminal(record, now)
        expired = [
            call_id
            for call_id, record in self._records.items()
            if record.phase == "terminal" and record.expires_at <= now
        ]
        for call_id in expired:
            self._records.pop(call_id, None)
        terminal_records = sorted(
            (
                (record.expires_at, call_id)
                for call_id, record in self._records.items()
                if record.phase == "terminal"
            )
        )
        excess = len(terminal_records) - self._max_terminal_tombstones
        for _, call_id in terminal_records[: max(0, excess)]:
            self._records.pop(call_id, None)

    @staticmethod
    def _require_log_id(value: str, *, label: str) -> None:
        if validated_log_id(value) is None:
            raise ValueError(f"{label} must be a canonical UUIDv4 hex value")
