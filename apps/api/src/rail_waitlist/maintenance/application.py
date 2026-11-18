from __future__ import annotations

import base64
import json
import os
import re
import secrets
import stat
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from ..config import get_settings
from .schemas import (
    ManagerStatus,
    UpdateAccepted,
    UpdateCatalog,
    UpdateJob,
    UpdateReason,
    UpdateStatus,
    UpdateSubmission,
)

MESSAGES: dict[UpdateReason, str] = {
    "managed_install_required": "설치 CLI로 설치한 서버에서 업데이트를 사용할 수 있습니다.",
    "manager_unavailable": "업데이트 관리 서비스의 연결을 확인해 주세요.",
    "checking": "업데이트 정보를 확인하고 있습니다.",
    "up_to_date": "최신 버전을 사용하고 있습니다.",
    "update_available": "새 버전을 설치할 수 있습니다.",
    "update_blocked": "지금은 업데이트를 진행할 수 없습니다.",
    "recovery_required": "이전 업데이트의 복구가 필요합니다.",
}


class ManagementUnavailable(Exception):
    pass


class UpdatePlanChanged(Exception):
    pass


def _read_file(path: Path, limit: int = 16384) -> bytes:
    descriptor = os.open(
        path, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    )
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("regular file required")
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("management file exceeds bound")
    return payload


def _fresh(value: datetime, seconds: int, now: datetime) -> bool:
    return value.tzinfo is not None and -5 <= (now - value).total_seconds() <= seconds


def update_status() -> UpdateStatus:
    configured = get_settings().railwait_management_dir
    if configured is None:
        return UpdateStatus(
            managed=False,
            manager_available=False,
            available=False,
            reason="managed_install_required",
            message=MESSAGES["managed_install_required"],
        )
    directory = Path(configured)
    now = datetime.now(UTC)
    manager_available = False
    try:
        manager = ManagerStatus.model_validate_json(_read_file(directory / "status/manager.json"))
        manager_available = manager.state == "ready" and _fresh(manager.heartbeat_at, 10, now)
    except (OSError, ValueError, ValidationError):
        pass
    catalog = None
    try:
        catalog = UpdateCatalog.model_validate_json(_read_file(directory / "status/catalog.json"))
        if not _fresh(catalog.checked_at, 1800, now):
            reason: UpdateReason = "checking"
        else:
            reason = catalog.reason
    except (OSError, ValueError, ValidationError):
        reason = "checking"
    if not manager_available:
        reason = "manager_unavailable"
    available = (
        manager_available
        and reason == "update_available"
        and catalog is not None
        and catalog.available
        and catalog.plan_id is not None
        and catalog.current_version is not None
        and catalog.latest_version is not None
        and catalog.current_version != catalog.latest_version
    )
    if (
        reason == "update_available"
        and not available
        or reason == "up_to_date"
        and catalog is not None
        and catalog.current_version is None
    ):
        reason = "update_blocked"
    return UpdateStatus(
        managed=True,
        manager_available=manager_available,
        checked_at=catalog.checked_at if catalog is not None else None,
        current_version=catalog.current_version if catalog is not None else None,
        latest_version=catalog.latest_version if catalog is not None else None,
        plan_id=catalog.plan_id if available and catalog is not None else None,
        available=available,
        reason=reason,
        message=MESSAGES[reason],
    )


def submit_update(data: UpdateSubmission) -> UpdateAccepted:
    status = update_status()
    if not status.manager_available:
        raise ManagementUnavailable()
    if (
        not status.available
        or status.latest_version != data.target_release_id
        or status.plan_id != data.plan_id
    ):
        raise UpdatePlanChanged()
    settings = get_settings()
    if settings.railwait_management_dir is None or settings.railwait_update_token_file is None:
        raise ManagementUnavailable()
    try:
        token = _read_file(Path(settings.railwait_update_token_file), 128).decode("ascii").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise ValueError("invalid management token")
        if len(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))) != 32:
            raise ValueError("invalid management token")
        identifier = secrets.token_hex(16)
        payload = json.dumps(
            {
                "schema": 1,
                "id": identifier,
                **data.model_dump(),
                "auth_token": token,
            }
        ).encode("utf-8")
        directory = Path(settings.railwait_management_dir) / "requests"
        temporary = directory / f".{identifier}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
                os.fchmod(stream.fileno(), 0o644)
            os.link(temporary, directory / f"{identifier}.json")
        finally:
            temporary.unlink(missing_ok=True)
    except (OSError, ValueError):
        raise ManagementUnavailable() from None
    return UpdateAccepted(id=identifier)


def update_job(identifier: str) -> UpdateJob | None:
    configured = get_settings().railwait_management_dir
    if configured is None or not re.fullmatch(r"[a-f0-9]{32}", identifier):
        return None
    try:
        job = UpdateJob.model_validate_json(
            _read_file(Path(configured) / f"status/jobs/{identifier}.json")
        )
        return job if job.id == identifier else None
    except FileNotFoundError:
        return None
    except (OSError, ValueError, ValidationError):
        raise ManagementUnavailable() from None
