"""Service restarts requested from the web settings window and run by the host manager.

The API only publishes the request. The manager validates the install .env, waits for
running work, recreates the app services and reports progress in an ordinary job record.
"""

from __future__ import annotations

from .application import ManagementUnavailable, publish_request
from .schemas import RestartAccepted, RestartSubmission
from .settings import settings_status


class RestartManagerUpgradeRequired(Exception):
    pass


def _request_document(identifier: str, token: str, data: RestartSubmission) -> dict[str, object]:
    return {
        "schema": 1,
        "id": identifier,
        "idempotency_key": data.idempotency_key,
        "auth_token": token,
        "scope": data.scope,
    }


def submit_restart(data: RestartSubmission) -> RestartAccepted:
    status = settings_status()
    if not status.manager_available:
        raise ManagementUnavailable()
    # Managers that do not announce the capability ignore *.restart.json, so refuse early.
    if not status.restart_available:
        raise RestartManagerUpgradeRequired()
    identifier = publish_request(
        lambda identifier, token: _request_document(identifier, token, data),
        ".restart.json",
    )
    return RestartAccepted(id=identifier)
