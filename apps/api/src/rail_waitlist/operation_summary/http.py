from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..database import get_session
from ..heartbeat import EMPTY_HEARTBEATS
from ..operations import build_operations_summary
from .schemas import OperationsSummary

router = APIRouter(prefix="/api/v1/operations", dependencies=[Depends(require_admin)])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/summary", response_model=OperationsSummary)
async def operations_summary(
    response: Response, request: Request, session: Session
) -> OperationsSummary:
    response.headers["Cache-Control"] = "no-store"
    reader = getattr(request.app.state, "heartbeat_reader", None)
    heartbeats = await reader.read() if reader is not None else EMPTY_HEARTBEATS
    return await build_operations_summary(session, heartbeats=heartbeats)
