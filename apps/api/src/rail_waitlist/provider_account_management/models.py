from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..domain import Provider


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def auth_revision_micros(moment: datetime) -> int:
    """Convert a persisted account timestamp into the revision microsecond identity."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1_000_000)


def active_protection_halt_version(
    *,
    credential_version: int,
    updated_at: datetime,
    halt_credential_version: int | None,
    halt_revision_micros: int | None,
) -> int | None:
    """Return the halted generation only while the halt still matches this row revision.

    Any authentication write rotates the row revision, so a halt that no longer matches
    has already been superseded by newer provider evidence. It expires on its own and
    never rewrites the authentication history.
    """
    if halt_credential_version is None or halt_revision_micros is None:
        return None
    if halt_credential_version != credential_version:
        return None
    if halt_revision_micros != auth_revision_micros(updated_at):
        return None
    return halt_credential_version


class RailProviderAccount(Base):
    __tablename__ = "rail_provider_accounts"
    __table_args__ = (
        CheckConstraint(
            "provider IN ('KORAIL', 'SRT')",
            name="ck_rail_provider_account_provider_allowed",
        ),
        CheckConstraint(
            "length(trim(credentials_ciphertext)) > 0",
            name="ck_rail_provider_account_ciphertext_nonempty",
        ),
        CheckConstraint(
            "credential_version >= 1",
            name="ck_rail_provider_account_version_positive",
        ),
        CheckConstraint(
            "last_auth_status IN ('not_checked', 'authenticated', 'auth_required', "
            "'provider_blocked', 'failed')",
            name="ck_rail_provider_account_auth_status_allowed",
        ),
        CheckConstraint(
            "(protection_halt_credential_version IS NULL) = "
            "(protection_halt_revision_micros IS NULL)",
            name="ck_rail_provider_account_protection_halt_pair",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    provider: Mapped[Provider] = mapped_column(
        Enum(Provider, native_enum=False), unique=True, index=True
    )
    credentials_ciphertext: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    credential_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    last_auth_status: Mapped[str] = mapped_column(
        String(32), default="not_checked", server_default="not_checked"
    )
    last_authenticated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # A keepalive login answered by provider automation protection fences exactly one
    # account revision: the credential generation plus the microsecond identity of
    # updated_at at verdict time. While both match this row, automatic keepalive logins
    # stay halted and observation stays deferred, even across an API restart. The auth
    # status keeps its existing meaning, and a newer authentication write expires the
    # halt simply by rotating updated_at.
    protection_halt_credential_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    protection_halt_revision_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
