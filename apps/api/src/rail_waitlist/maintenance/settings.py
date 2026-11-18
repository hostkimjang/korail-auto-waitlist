"""Install .env settings exchanged with the host manager through management files.

The API never reads the install .env. It only projects the manager's secret-free
snapshot and publishes change requests that the manager validates and applies.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from pydantic import ValidationError

from ..config import get_settings
from .application import (
    ManagementUnavailable,
    live_manager,
    publish_request,
    read_status_file,
)
from .schemas import (
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
