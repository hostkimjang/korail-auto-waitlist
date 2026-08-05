from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..database import get_session
from ..models import AdminAccount
from .application import update_admin_ui_preferences
from .schemas import UiPreferencesRead, UiPreferencesUpdate

router = APIRouter(prefix="/api/v1/preferences", dependencies=[Depends(require_admin)])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/ui", response_model=UiPreferencesRead)
async def ui_preferences(response: Response, session: Session) -> AdminAccount:
    response.headers["Cache-Control"] = "no-store"
    account = await session.scalar(select(AdminAccount).where(AdminAccount.singleton_slot == 1))
    if account is None:
        raise HTTPException(404, "administrator account was not found")
    return account


@router.patch("/ui", response_model=UiPreferencesRead)
async def ui_preferences_update(
    data: UiPreferencesUpdate,
    response: Response,
    session: Session,
) -> AdminAccount:
    response.headers["Cache-Control"] = "no-store"
    account = await session.scalar(
        select(AdminAccount).where(AdminAccount.singleton_slot == 1).with_for_update()
    )
    if account is None:
        raise HTTPException(404, "administrator account was not found")
    return await update_admin_ui_preferences(session, account, data)
