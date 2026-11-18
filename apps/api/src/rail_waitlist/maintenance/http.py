from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from ..auth import require_admin
from .application import (
    ManagementUnavailable,
    UpdatePlanChanged,
    submit_update,
    update_job,
    update_status,
)
from .schemas import (
    SettingsAccepted,
    SettingsStatus,
    SettingsSubmission,
    UpdateAccepted,
    UpdateJob,
    UpdateStatus,
    UpdateSubmission,
)
from .settings import (
    SettingsChanged,
    SettingsManagerUpgradeRequired,
    settings_status,
    submit_settings,
)

router = APIRouter(prefix="/api/v1/system/update", dependencies=[Depends(require_admin)])
settings_router = APIRouter(prefix="/api/v1/system/settings", dependencies=[Depends(require_admin)])
NO_STORE = {"Cache-Control": "no-store"}

# The handlers below are plain functions so FastAPI runs them in its thread pool: reading the
# manager's status files may wait briefly for a file replaced by rename to become visible.


@router.get("", response_model=UpdateStatus)
def read_update_status(response: Response) -> UpdateStatus:
    response.headers["Cache-Control"] = "no-store"
    return update_status()


@router.post("", response_model=UpdateAccepted, status_code=202)
def request_update(data: UpdateSubmission, response: Response) -> UpdateAccepted:
    response.headers["Cache-Control"] = "no-store"
    try:
        return submit_update(data)
    except ManagementUnavailable:
        raise HTTPException(503, "update_manager_unavailable") from None
    except UpdatePlanChanged:
        raise HTTPException(409, "update_plan_changed") from None


@router.get("/jobs/{identifier}", response_model=UpdateJob)
def read_update_job(identifier: str, response: Response) -> UpdateJob:
    response.headers["Cache-Control"] = "no-store"
    try:
        job = update_job(identifier)
    except ManagementUnavailable:
        raise HTTPException(503, "update_manager_unavailable") from None
    if job is None:
        raise HTTPException(404, "update_job_not_found")
    return job


@settings_router.get("", response_model=SettingsStatus)
def read_settings_status(response: Response) -> SettingsStatus:
    response.headers["Cache-Control"] = "no-store"
    return settings_status()


@settings_router.post("", response_model=SettingsAccepted, status_code=202)
def request_settings_change(data: SettingsSubmission, response: Response) -> SettingsAccepted:
    response.headers["Cache-Control"] = "no-store"
    try:
        return submit_settings(data)
    except ManagementUnavailable:
        raise HTTPException(503, "settings_manager_unavailable", headers=NO_STORE) from None
    except SettingsManagerUpgradeRequired:
        raise HTTPException(409, "settings_manager_upgrade_required", headers=NO_STORE) from None
    except SettingsChanged:
        raise HTTPException(409, "settings_changed", headers=NO_STORE) from None
