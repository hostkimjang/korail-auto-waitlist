from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..domain import Provider, ProviderCircuitState


def utcnow() -> datetime:
    return datetime.now(UTC)


class ProviderCircuit(Base):
    __tablename__ = "provider_circuits"
    __table_args__ = (
        CheckConstraint(
            "reason IS NULL OR length(trim(reason)) > 0",
            name="ck_provider_circuit_reason_nonempty",
        ),
        CheckConstraint(
            "cooldown_until IS NULL OR opened_at IS NULL OR cooldown_until >= opened_at",
            name="ck_provider_circuit_cooldown_order",
        ),
        CheckConstraint("generation >= 0", name="ck_provider_circuit_generation_nonnegative"),
        CheckConstraint(
            "provider IN ('KORAIL', 'SRT', 'MOCK')",
            name="ck_provider_circuit_provider_allowed",
        ),
        CheckConstraint(
            "state IN ('CLOSED', 'OPEN', 'HALF_OPEN', 'MANUAL_HOLD')",
            name="ck_provider_circuit_state_allowed",
        ),
        Index("ix_provider_circuits_state_cooldown", "state", "cooldown_until"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    provider: Mapped[Provider] = mapped_column(Enum(Provider, native_enum=False), unique=True)
    state: Mapped[ProviderCircuitState] = mapped_column(
        Enum(ProviderCircuitState, native_enum=False), default=ProviderCircuitState.CLOSED
    )
    reason: Mapped[str | None] = mapped_column(String(160), nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    manual_resume_required: Mapped[bool] = mapped_column(Boolean, default=False)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
