from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..domain import Provider


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
