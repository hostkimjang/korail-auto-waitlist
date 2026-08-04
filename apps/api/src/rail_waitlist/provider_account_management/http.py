from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..database import get_session
from ..domain import Provider
from ..provider_accounts import (
    ProviderAccountGenerationConflict,
    ProviderCredentials,
    delete_provider_account,
    get_next_provider_credential_version,
    list_provider_accounts,
    upsert_provider_account,
)
from ..provider_login_verification import ProviderLoginVerificationOutcome
from .schemas import (
    RailProviderAccountRead,
    RailProviderAccountUpsert,
    RailProviderRuntimeStatusRead,
)

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_admin)])
Session = Annotated[AsyncSession, Depends(get_session)]
ProviderAccountPath = Literal[Provider.KORAIL, Provider.SRT]


@router.get("/provider-accounts", response_model=list[RailProviderAccountRead])
async def provider_accounts_list(
    response: Response,
    session: Session,
) -> list[RailProviderAccountRead]:
    response.headers["Cache-Control"] = "no-store"
    return await list_provider_accounts(session)


@router.get(
    "/provider-runtime-status",
    response_model=list[RailProviderRuntimeStatusRead],
)
async def provider_runtime_status(
    request: Request,
    response: Response,
) -> list[RailProviderRuntimeStatusRead]:
    """Expose only process-local session telemetry and sanitized startup outcomes."""

    response.headers["Cache-Control"] = "no-store"
    registry = request.app.state.provider_runtime_prewarm_registry
    results: list[RailProviderRuntimeStatusRead] = []
    for provider in (Provider.KORAIL, Provider.SRT):
        try:
            snapshot = await request.app.state.provider_login_verifier.session_snapshot(provider)
            results.append(
                RailProviderRuntimeStatusRead(
                    provider=provider,
                    state=snapshot.state.value,
                    credential_generation=snapshot.credential_generation,
                    created_age_seconds=snapshot.created_age_seconds,
                    last_verified_age_seconds=snapshot.last_verified_age_seconds,
                    last_used_age_seconds=snapshot.last_used_age_seconds,
                    local_reuse_remaining_seconds=snapshot.local_reuse_remaining_seconds,
                    locally_reusable=snapshot.locally_reusable,
                    prewarm_outcome=registry.outcome_for(provider),
                )
            )
        except Exception:  # noqa: BLE001 -- provider exceptions must remain redacted.
            results.append(
                RailProviderRuntimeStatusRead(
                    provider=provider,
                    state="stale",
                    locally_reusable=False,
                    prewarm_outcome=registry.outcome_for(provider),
                )
            )
    return results


@router.put("/provider-accounts/{provider}", response_model=RailProviderAccountRead)
async def provider_accounts_upsert(
    provider: ProviderAccountPath,
    data: RailProviderAccountUpsert,
    request: Request,
    response: Response,
    session: Session,
) -> RailProviderAccountRead:
    response.headers["Cache-Control"] = "no-store"
    verified_credential_version = await get_next_provider_credential_version(session, provider)
    # Do not retain a read transaction while the external login verification is in progress.
    # The upsert below re-reads under a row lock and compares this generation before writing.
    await session.rollback()
    verification = await request.app.state.provider_login_verifier.verify(
        provider,
        ProviderCredentials(
            login_method=data.login_method,
            login_id=data.login_id,
            password=data.password.get_secret_value(),
            credential_version=verified_credential_version,
        ),
    )
    if not verification.authenticated:
        details = {
            ProviderLoginVerificationOutcome.INVALID_IDENTIFIER: (
                422,
                "선택한 로그인 방식과 계정 정보 형식을 확인해 주세요.",
            ),
            ProviderLoginVerificationOutcome.AUTH_REQUIRED: (
                422,
                "철도사 로그인에 실패했습니다. 계정 정보와 로그인 방식을 확인해 주세요.",
            ),
            ProviderLoginVerificationOutcome.PROVIDER_BLOCKED: (
                503,
                "철도사가 현재 로그인 확인을 제한했습니다. 잠시 후 직접 다시 시도해 주세요.",
            ),
            ProviderLoginVerificationOutcome.FAILED: (
                503,
                "철도사 로그인 확인 응답을 받지 못했습니다. 계정은 저장되지 않았습니다.",
            ),
        }
        status_code, detail = details.get(
            verification.outcome,
            (503, "철도사 로그인 확인을 완료하지 못했습니다."),
        )
        raise HTTPException(status_code, detail)
    try:
        return await upsert_provider_account(
            session,
            provider,
            data,
            verified_credential_version=verified_credential_version,
        )
    except ProviderAccountGenerationConflict:
        raise HTTPException(
            409,
            "철도 계정이 로그인 확인 중 변경되었습니다. 다시 시도해 주세요.",
        ) from None


@router.delete("/provider-accounts/{provider}", status_code=204)
async def provider_accounts_delete(
    provider: ProviderAccountPath,
    response: Response,
    session: Session,
) -> Response:
    await delete_provider_account(session, provider)
    response.headers["Cache-Control"] = "no-store"
    response.status_code = 204
    return response
