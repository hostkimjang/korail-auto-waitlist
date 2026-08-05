from fastapi import APIRouter, Depends

from ..auth import require_admin
from ..schemas import ProviderCapabilities
from .application import list_capabilities

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_admin)])


@router.get("/providers", response_model=list[ProviderCapabilities])
async def providers() -> list[ProviderCapabilities]:
    return list_capabilities()
