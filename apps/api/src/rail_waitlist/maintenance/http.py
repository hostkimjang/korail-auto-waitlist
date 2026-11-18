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
from .schemas import UpdateAccepted, UpdateJob, UpdateStatus, UpdateSubmission

router = APIRouter(prefix="/api/v1/system/update", dependencies=[Depends(require_admin)])


@router.get("", response_model=UpdateStatus)
async def read_update_status(response: Response) -> UpdateStatus:
    response.headers["Cache-Control"] = "no-store"
    return update_status()


@router.post("", response_model=UpdateAccepted, status_code=202)
async def request_update(data: UpdateSubmission, response: Response) -> UpdateAccepted:
    response.headers["Cache-Control"] = "no-store"
    try:
        return submit_update(data)
    except ManagementUnavailable:
        raise HTTPException(503, "update_manager_unavailable") from None
    except UpdatePlanChanged:
        raise HTTPException(409, "update_plan_changed") from None


@router.get("/jobs/{identifier}", response_model=UpdateJob)
async def read_update_job(identifier: str, response: Response) -> UpdateJob:
    response.headers["Cache-Control"] = "no-store"
    try:
        job = update_job(identifier)
    except ManagementUnavailable:
        raise HTTPException(503, "update_manager_unavailable") from None
    if job is None:
        raise HTTPException(404, "update_job_not_found")
    return job
