from __future__ import annotations

import typing as _typing
from dataclasses import dataclass as dataclass
from dataclasses import field as field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import Provider
from ..security import secret_box
from . import contracts as _account_contracts
from .models import RailProviderAccount
from .schemas import (
    RailProviderAccountRead,
    RailProviderAccountUpsert,
    RailProviderAuthStatus,
)

ProviderCredentials = _account_contracts.ProviderCredentials
RailLoginMethod = _account_contracts.RailLoginMethod

SUPPORTED_ACCOUNT_PROVIDERS = (Provider.KORAIL, Provider.SRT)
PROVIDER_AUTH_STATUSES: tuple[RailProviderAuthStatus, ...] = (
    "not_checked",
    "authenticated",
    "auth_required",
    "provider_blocked",
    "failed",
)


class ProviderAccountGenerationConflict(RuntimeError):
    """The provider account changed while its new credentials were being verified."""


def _require_supported_provider(provider: Provider) -> None:
    if provider not in SUPPORTED_ACCOUNT_PROVIDERS:
        raise ValueError("rail provider accounts support only KORAIL and SRT")


def _mask_login_id(login_id: str) -> str:
    if len(login_id) <= 2:
        return "*" * len(login_id)
    return f"{login_id[0]}{'*' * (len(login_id) - 2)}{login_id[-1]}"


def _infer_legacy_login_method(login_id: str) -> RailLoginMethod:
    """Keep old encrypted rows readable until the administrator re-saves them explicitly."""
    if "@" in login_id:
        return "email"
    digits = "".join(character for character in login_id if character.isdigit())
    if login_id.replace("-", "").isdigit() and len(digits) in {10, 11} and digits.startswith("01"):
        return "phone"
    return "membership_number"


def _decrypt_credentials(account: RailProviderAccount) -> ProviderCredentials:
    try:
        payload = secret_box.decrypt_dict(account.credentials_ciphertext)
        login_id = payload.get("login_id")
        password = payload.get("password")
        raw_login_method = payload.get("login_method")
    except RuntimeError as error:
        raise RuntimeError("stored rail provider credentials cannot be decrypted") from error
    if not isinstance(login_id, str) or not login_id:
        raise RuntimeError("stored rail provider credentials are invalid")
    if not isinstance(password, str) or not password:
        raise RuntimeError("stored rail provider credentials are invalid")
    return ProviderCredentials(
        login_method=(
            raw_login_method
            if raw_login_method in {"membership_number", "email", "phone"}
            else _infer_legacy_login_method(login_id)
        ),
        login_id=login_id,
        password=password,
        credential_version=account.credential_version,
    )


async def get_enabled_provider_credentials(
    session: AsyncSession,
    provider: Provider,
) -> ProviderCredentials | None:
    _require_supported_provider(provider)
    account = await session.scalar(
        select(RailProviderAccount).where(
            RailProviderAccount.provider == provider,
            RailProviderAccount.enabled.is_(True),
        )
    )
    return _decrypt_credentials(account) if account is not None else None


async def has_authenticated_provider_account(
    session: AsyncSession,
    provider: Provider,
    *,
    lock: bool = False,
) -> bool:
    """Check the reservation dispatch gate without decrypting stored credentials."""
    _require_supported_provider(provider)
    query = select(RailProviderAccount.id).where(
        RailProviderAccount.provider == provider,
        RailProviderAccount.enabled.is_(True),
        RailProviderAccount.last_auth_status == "authenticated",
    )
    if lock:
        # Serialize the final reservation gate with provider-account status updates.
        # No credentials or browser session material are read at this boundary.
        query = query.with_for_update()
    account_id = await session.scalar(query)
    return account_id is not None


def provider_account_read(account: RailProviderAccount) -> RailProviderAccountRead:
    credentials = _decrypt_credentials(account)
    return RailProviderAccountRead(
        provider=_typing.cast(
            _typing.Literal[Provider.KORAIL, Provider.SRT],
            account.provider,
        ),
        configured=True,
        enabled=account.enabled,
        login_method=credentials.login_method,
        masked_login_id=_mask_login_id(credentials.login_id),
        credential_version=account.credential_version,
        last_auth_status=_typing.cast(RailProviderAuthStatus, account.last_auth_status),
        last_authenticated_at=account.last_authenticated_at,
        updated_at=account.updated_at,
    )


def unconfigured_provider_account_read(provider: Provider) -> RailProviderAccountRead:
    _require_supported_provider(provider)
    return RailProviderAccountRead(
        provider=_typing.cast(_typing.Literal[Provider.KORAIL, Provider.SRT], provider),
        configured=False,
        enabled=False,
        login_method=None,
        masked_login_id=None,
        credential_version=0,
        last_auth_status="not_checked",
        last_authenticated_at=None,
        updated_at=None,
    )


async def list_provider_accounts(session: AsyncSession) -> list[RailProviderAccountRead]:
    rows = list(
        (
            await session.scalars(
                select(RailProviderAccount).where(
                    RailProviderAccount.provider.in_(SUPPORTED_ACCOUNT_PROVIDERS)
                )
            )
        ).all()
    )
    by_provider = {row.provider: row for row in rows}
    return [
        provider_account_read(by_provider[provider])
        if provider in by_provider
        else unconfigured_provider_account_read(provider)
        for provider in SUPPORTED_ACCOUNT_PROVIDERS
    ]


async def get_next_provider_credential_version(
    session: AsyncSession,
    provider: Provider,
) -> int:
    """Read the generation that a successfully verified credential update must persist."""
    _require_supported_provider(provider)
    current_version = await session.scalar(
        select(RailProviderAccount.credential_version).where(
            RailProviderAccount.provider == provider
        )
    )
    return 1 if current_version is None else current_version + 1


async def upsert_provider_account(
    session: AsyncSession,
    provider: Provider,
    data: RailProviderAccountUpsert,
    *,
    verified_credential_version: int,
) -> RailProviderAccountRead:
    _require_supported_provider(provider)
    account = await session.scalar(
        select(RailProviderAccount)
        .where(RailProviderAccount.provider == provider)
        .with_for_update()
    )
    current_version = account.credential_version if account is not None else 0
    if verified_credential_version != current_version + 1:
        await session.rollback()
        raise ProviderAccountGenerationConflict(
            "rail provider account changed during login verification"
        )
    ciphertext = secret_box.encrypt_dict(
        {
            "login_method": data.login_method,
            "login_id": data.login_id,
            "password": data.password.get_secret_value(),
        }
    )
    now = datetime.now(UTC)
    if account is None:
        account = RailProviderAccount(
            provider=provider,
            credentials_ciphertext=ciphertext,
            enabled=data.enabled,
            credential_version=verified_credential_version,
            last_auth_status="authenticated",
            last_authenticated_at=now,
            updated_at=now,
        )
        session.add(account)
    else:
        account.credentials_ciphertext = ciphertext
        account.enabled = data.enabled
        account.credential_version = verified_credential_version
        account.last_auth_status = "authenticated"
        account.last_authenticated_at = now
        account.updated_at = now
    # Import locally to keep the provider-account encryption boundary independent
    # from watch/provider registry composition at import time.
    from .auth_recovery_runtime import resume_watches_after_verified_provider_login

    try:
        # ``resume_watches...`` can issue queries that autoflush a first-time insert, so the
        # uniqueness race and the final commit must share the same conflict boundary.
        await resume_watches_after_verified_provider_login(session, provider, now)
        await session.commit()
    except IntegrityError as error:
        # Two first-time account updates can both verify generation 1 before either inserts.
        # The provider uniqueness constraint is the final fail-closed compare-and-swap gate.
        await session.rollback()
        raise ProviderAccountGenerationConflict(
            "rail provider account changed during login verification"
        ) from error
    await session.refresh(account)
    return provider_account_read(account)


async def delete_provider_account(session: AsyncSession, provider: Provider) -> None:
    _require_supported_provider(provider)
    account = await session.scalar(
        select(RailProviderAccount)
        .where(RailProviderAccount.provider == provider)
        .with_for_update()
    )
    if account is not None:
        await session.delete(account)
        await session.commit()


async def update_provider_auth_status(
    session: AsyncSession,
    provider: Provider,
    status: RailProviderAuthStatus,
    *,
    expected_credential_version: int | None = None,
    commit: bool = True,
) -> RailProviderAccountRead | None:
    """Persist sanitized auth metadata for the credential generation that produced it."""
    _require_supported_provider(provider)
    if status not in PROVIDER_AUTH_STATUSES:
        raise ValueError("unsupported rail provider authentication status")
    account = await session.scalar(
        select(RailProviderAccount)
        .where(RailProviderAccount.provider == provider)
        .with_for_update()
    )
    if account is None:
        return None

    if (
        expected_credential_version is not None
        and account.credential_version != expected_credential_version
    ):
        # An external reservation may finish after the administrator verified and
        # saved a newer credential generation. Its stale result must not demote the
        # newly authenticated account or refresh its successful-auth timestamp.
        if commit:
            await session.commit()
            await session.refresh(account)
        return provider_account_read(account)

    now = datetime.now(UTC)
    account.last_auth_status = status
    if status == "authenticated":
        account.last_authenticated_at = now
    # Failure states intentionally retain the last successful authentication timestamp.
    account.updated_at = now
    if commit:
        await session.commit()
        await session.refresh(account)
    else:
        await session.flush()
    return provider_account_read(account)
