from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Enum, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..domain import Provider


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProviderExecutionLease(Base):
    __tablename__ = "provider_execution_leases"
    __table_args__ = (
        CheckConstraint(
            "provider IN ('KORAIL', 'SRT')",
            name="ck_provider_execution_lease_provider_allowed",
        ),
        CheckConstraint(
            "length(trim(account_scope)) > 0",
            name="ck_provider_execution_lease_scope_nonempty",
        ),
        CheckConstraint(
            "fencing_token >= 1",
            name="ck_provider_execution_lease_fencing_positive",
        ),
        CheckConstraint(
            "((owner_token IS NULL AND expires_at IS NULL) OR "
            "(owner_token IS NOT NULL AND expires_at IS NOT NULL))",
            name="ck_provider_execution_lease_owner_expiry_shape",
        ),
        Index("ix_provider_execution_leases_expires_at", "expires_at"),
    )

    provider: Mapped[Provider] = mapped_column(Enum(Provider, native_enum=False), primary_key=True)
    account_scope: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
