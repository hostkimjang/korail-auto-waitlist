from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import Provider
from ..watch_management.transition_runtime import apply_watch_transition
from .auth_recovery_application import ProviderAuthRecoveryDependencies
from .auth_recovery_application import (
    resume_watches_after_verified_provider_login as resume_watches_application,
)


async def resume_watches_after_verified_provider_login(
    session: AsyncSession,
    provider: Provider,
    authenticated_at: datetime,
    *,
    credential_version: int | None = None,
) -> list[str]:
    """Resume verified watches through the feature-owned transition runtime."""
    return await resume_watches_application(
        session,
        provider,
        authenticated_at,
        credential_version=credential_version,
        dependencies=ProviderAuthRecoveryDependencies(
            apply_watch_transition=apply_watch_transition,
        ),
    )
