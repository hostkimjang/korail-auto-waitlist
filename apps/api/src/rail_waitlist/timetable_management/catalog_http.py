from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import require_admin
from ..domain import Provider
from ..provider_adapters.timetable import OfficialTimetableAdapter
from ..provider_contracts import ProviderUnavailable
from ..provider_registry.application import get_timetable_provider
from ..schemas import StationCatalog

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_admin)])


@router.get("/stations", response_model=StationCatalog)
async def stations(provider: Provider, request: Request) -> StationCatalog:
    try:
        adapter = get_timetable_provider(provider)
        if isinstance(adapter, OfficialTimetableAdapter):
            return await request.app.state.station_catalog_service.get_catalog(provider)
        return await adapter.stations()
    except ProviderUnavailable as error:
        raise HTTPException(503, str(error)) from None
