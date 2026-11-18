from __future__ import annotations

import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

if os.name == "posix":
    import fcntl

MAX_CONTROL_BYTES = 4096


class MaintenanceClosed(Exception):
    def __init__(self) -> None:
        super().__init__("maintenance_in_progress")


@dataclass(frozen=True)
class MaintenanceState:
    closed: bool
    generation: str


class MaintenanceGate:
    def __init__(self, directory: str | Path | None) -> None:
        self.directory = Path(directory) if directory is not None else None

    @classmethod
    def configured(cls) -> MaintenanceGate:
        from ..config import get_settings

        return cls(get_settings().railwait_management_dir)

    def _read_only_fd(self, relative: str) -> int:
        if self.directory is None or os.name != "posix":
            raise MaintenanceClosed()
        try:
            descriptor = os.open(
                self.directory / relative,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
            )
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                os.close(descriptor)
                raise MaintenanceClosed()
            return descriptor
        except OSError:
            raise MaintenanceClosed() from None

    def state(self) -> MaintenanceState:
        if self.directory is None:
            return MaintenanceState(closed=False, generation="unmanaged")
        descriptor = self._read_only_fd("control/maintenance.json")
        try:
            with os.fdopen(descriptor, "rb") as stream:
                payload = stream.read(MAX_CONTROL_BYTES + 1)
            if len(payload) > MAX_CONTROL_BYTES:
                raise ValueError("control exceeds bound")
            data = json.loads(payload)
            if (
                not isinstance(data, dict)
                or set(data) != {"schema", "closed", "generation"}
                or type(data["schema"]) is not int
                or data["schema"] != 1
                or type(data["closed"]) is not bool
                or not isinstance(data["generation"], str)
                or not 1 <= len(data["generation"]) <= 128
                or not data["generation"].isascii()
                or not all(
                    character.isalnum() or character in "._-" for character in data["generation"]
                )
            ):
                raise ValueError("invalid maintenance control")
            return MaintenanceState(closed=data["closed"], generation=data["generation"])
        except (OSError, ValueError):
            raise MaintenanceClosed() from None

    @contextmanager
    def operation(self) -> Iterator[None]:
        if self.directory is None:
            yield
            return
        descriptor = self._read_only_fd("gate/operation.lock")
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except OSError:
                raise MaintenanceClosed() from None
            if self.state().closed:
                raise MaintenanceClosed()
            yield
        finally:
            os.close(descriptor)

    def is_closed(self) -> bool:
        try:
            return self.state().closed
        except MaintenanceClosed:
            return True

    def status(self) -> dict[str, str | bool | int]:
        state = self.state()
        drained = True
        if self.directory is not None:
            descriptor = self._read_only_fd("gate/operation.lock")
            try:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    drained = False
            finally:
                os.close(descriptor)
        return {
            "schema": 1,
            "closed": state.closed,
            "generation": state.generation,
            "drained": drained,
        }
