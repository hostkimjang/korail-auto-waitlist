"""Install .env settings exchanged with the host manager through management files.

The API never reads the install .env. It only projects the manager's secret-free
snapshot and publishes change requests that the manager validates and applies.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from pydantic import ValidationError

from ..config import get_settings
from .application import (
    ManagementUnavailable,
    live_manager,
    publish_request,
    read_management_file,
    read_status_file,
)
from .schemas import (
    HostOs,
    InstallLocation,
    ManagerCapabilities,
    ManagerStatus,
    SettingKey,
    SettingKind,
    SettingsAccepted,
    SettingScope,
    SettingsField,
    SettingsReason,
    SettingsSnapshot,
    SettingsSnapshotSecret,
    SettingsSnapshotValue,
    SettingsStatus,
    SettingsSubmission,
)

SNAPSHOT_FILE_LIMIT = 256 * 1024
CAPABILITIES_FILE_LIMIT = 16 * 1024
RESTART_CAPABILITY = "restart"
_INSTALL_DIR_LINE = re.compile(r"^RAILWAIT_INSTALL_DIR=(.*)$", re.MULTILINE)
_WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")


class SettingDefinition(NamedTuple):
    key: SettingKey
    kind: SettingKind
    scope: SettingScope


SETTING_DEFINITIONS: tuple[SettingDefinition, ...] = (
    SettingDefinition("KORAIL_RESERVATION_ONCE_ENABLED", "boolean", "app"),
    SettingDefinition("SRT_RESERVATION_ONCE_ENABLED", "boolean", "app"),
    SettingDefinition("TAGO_SERVICE_KEY", "secret", "app"),
    SettingDefinition("WEBPUSH_VAPID_SUBJECT", "contact", "app"),
    SettingDefinition("VITE_KORAIL_BOOKING_DEEPLINK_ENABLED", "boolean", "web"),
    SettingDefinition("VITE_KORAIL_BOOKING_VALIDATED_VERSION", "version", "web"),
    SettingDefinition("VITE_KORAIL_TICKET_DEEPLINK_ENABLED", "boolean", "web"),
    SettingDefinition("VITE_KORAIL_TICKET_VALIDATED_VERSION", "version", "web"),
    SettingDefinition("VITE_SRT_MAIN_DEEPLINK_ENABLED", "boolean", "web"),
    SettingDefinition("VITE_SRT_MAIN_VALIDATED_VERSION", "version", "web"),
    SettingDefinition("VITE_SRT_TICKET_DEEPLINK_ENABLED", "boolean", "web"),
    SettingDefinition("VITE_SRT_TICKET_VALIDATED_VERSION", "version", "web"),
)


class SettingsManagerUpgradeRequired(Exception):
    pass


class SettingsChanged(Exception):
    pass


class _CurrentSettings(NamedTuple):
    snapshot: SettingsSnapshot
    fields: list[SettingsField]


def _project_field(definition: SettingDefinition, entries: dict[str, object]) -> SettingsField:
    secret = definition.kind == "secret"
    if definition.key not in entries:
        value: str | None = None if secret else ""
        configured = False
    elif secret:
        # A snapshot that exposes the secret value fails validation here (extra="forbid").
        configured = SettingsSnapshotSecret.model_validate(entries[definition.key]).configured
        value = None
    else:
        entry = SettingsSnapshotValue.model_validate(entries[definition.key])
        value, configured = entry.value, entry.configured
    return SettingsField(
        key=definition.key,
        kind=definition.kind,
        scope=definition.scope,
        value=value,
        configured=configured,
    )


def _current_settings(directory: Path, manager: ManagerStatus) -> _CurrentSettings | None:
    """Read the snapshot written by the live manager process, or None if it is unusable."""
    try:
        snapshot = SettingsSnapshot.model_validate_json(
            read_status_file(directory / "status/settings.json", SNAPSHOT_FILE_LIMIT)
        )
        fields = [_project_field(definition, snapshot.fields) for definition in SETTING_DEFINITIONS]
    except (OSError, ValueError, ValidationError):
        return None
    # A snapshot left behind by an earlier manager process may describe an older .env.
    if manager.started_at.tzinfo is None or snapshot.manager_started_at != manager.started_at:
        return None
    return _CurrentSettings(snapshot, fields)


def _unavailable(
    reason: SettingsReason, *, managed: bool, manager_available: bool
) -> SettingsStatus:
    return SettingsStatus(
        managed=managed,
        manager_available=manager_available,
        available=False,
        reason=reason,
    )


def _manager_capabilities(directory: Path, manager: ManagerStatus) -> ManagerCapabilities | None:
    """Capabilities of the live manager process, or None for a manager that predates them."""
    try:
        # Read once: older managers never write this file, and waiting for it on every
        # request would slow the settings screen down for them.
        capabilities = ManagerCapabilities.model_validate_json(
            read_management_file(directory / "status/capabilities.json", CAPABILITIES_FILE_LIMIT)
        )
    except (OSError, ValueError, ValidationError):
        return None
    # A file left behind by an earlier manager process may describe another executable.
    if manager.started_at.tzinfo is None or capabilities.manager_started_at != manager.started_at:
        return None
    return capabilities


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _usable_install_path(path: str) -> bool:
    return (
        0 < len(path) <= 4096
        and not any(ord(character) < 32 for character in path)
        and (path.startswith("/") or _WINDOWS_PATH.match(path) is not None)
    )


def _guess_host_os(path: str) -> HostOs | None:
    if _WINDOWS_PATH.match(path):
        return "windows"
    if path.startswith("/Users/"):
        return "darwin"
    if path.startswith("/home/"):
        return "linux"
    return None


def _install_location(capabilities: ManagerCapabilities | None, raw: str) -> InstallLocation | None:
    """Locate the install .env from the manager, or from the snapshot of older managers."""
    host_os: HostOs | None
    if capabilities is not None and _usable_install_path(capabilities.install_path):
        path, host_os = capabilities.install_path, capabilities.host_os
    else:
        # RAILWAIT_INSTALL_DIR is a locked, non-secret key every managed install writes.
        match = _INSTALL_DIR_LINE.search(raw)
        if match is None:
            return None
        path = _unquote(match.group(1))
        if not _usable_install_path(path):
            return None
        host_os = _guess_host_os(path)
    separator = "\\" if host_os == "windows" or _WINDOWS_PATH.match(path) else "/"
    return InstallLocation(
        install_path=path,
        env_path=path.rstrip("\\/") + separator + ".env",
        host_os=host_os,
    )


def settings_status() -> SettingsStatus:
    configured = get_settings().railwait_management_dir
    if configured is None:
        return _unavailable("managed_install_required", managed=False, manager_available=False)
    directory = Path(configured)
    manager = live_manager(directory, datetime.now(UTC))
    if manager is None:
        return _unavailable("manager_unavailable", managed=True, manager_available=False)
    current = _current_settings(directory, manager)
    if current is None:
        return _unavailable("manager_upgrade_required", managed=True, manager_available=True)
    capabilities = _manager_capabilities(directory, manager)
    return SettingsStatus(
        managed=True,
        manager_available=True,
        available=True,
        reason="ready",
        revision=current.snapshot.revision,
        fields=current.fields,
        raw=current.snapshot.raw,
        secret_keys=current.snapshot.secret_keys,
        locked_keys=current.snapshot.locked_keys,
        restart_available=(
            capabilities is not None and RESTART_CAPABILITY in capabilities.capabilities
        ),
        install_location=_install_location(capabilities, current.snapshot.raw),
    )


def _request_document(identifier: str, token: str, data: SettingsSubmission) -> dict[str, object]:
    document: dict[str, object] = {
        "schema": 1,
        "id": identifier,
        "idempotency_key": data.idempotency_key,
        "auth_token": token,
        "base_revision": data.base_revision,
    }
    if data.raw is not None:
        document["raw"] = data.raw.get_secret_value()
    elif data.fields is not None:
        document["fields"] = data.fields.submitted()
    return document


def submit_settings(data: SettingsSubmission) -> SettingsAccepted:
    status = settings_status()
    if not status.manager_available:
        raise ManagementUnavailable()
    # Managers without a current snapshot ignore *.settings.json requests, so refuse early.
    if not status.available:
        raise SettingsManagerUpgradeRequired()
    if status.revision != data.base_revision:
        raise SettingsChanged()
    identifier = publish_request(
        lambda identifier, token: _request_document(identifier, token, data),
        ".settings.json",
    )
    return SettingsAccepted(id=identifier)
