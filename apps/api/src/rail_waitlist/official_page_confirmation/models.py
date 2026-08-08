from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..domain import Provider, SeatClass, SeatObservationStatus


def utcnow() -> datetime:
    return datetime.now(UTC)


class OfficialPageSeatConfirmation(Base):
    __tablename__ = "official_page_seat_confirmations"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "seat_class",
            name="uq_official_page_confirmation_batch_seat_class",
        ),
        CheckConstraint(
            "provider IN ('KORAIL', 'SRT')",
            name="ck_official_page_confirmation_provider",
        ),
        CheckConstraint(
            "seat_class IN ('STANDARD', 'FIRST')",
            name="ck_official_page_confirmation_seat_class",
        ),
        CheckConstraint(
            "status IN ('AVAILABLE', 'SOLD_OUT', 'WAITLIST_AVAILABLE', 'NOT_OFFERED')",
            name="ck_official_page_confirmation_status",
        ),
        CheckConstraint(
            "source = 'official-page-user-confirmation'",
            name="ck_official_page_confirmation_source",
        ),
        CheckConstraint(
            "fresh_until > observed_at",
            name="ck_official_page_confirmation_freshness_order",
        ),
        CheckConstraint(
            "passenger_count BETWEEN 1 AND 9",
            name="ck_official_page_confirmation_passenger_count",
        ),
        Index(
            "ix_official_page_confirmation_route_fresh",
            "provider",
            "origin_node_id",
            "destination_node_id",
            "passenger_count",
            "departure_at",
            "fresh_until",
            "observed_at",
        ),
        Index("ix_official_page_confirmation_batch_id", "batch_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    batch_id: Mapped[str] = mapped_column(String(36))
    provider: Mapped[Provider] = mapped_column(Enum(Provider, native_enum=False))
    origin_node_id: Mapped[str] = mapped_column(String(80))
    destination_node_id: Mapped[str] = mapped_column(String(80))
    train_number: Mapped[str] = mapped_column(String(40))
    departure_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    passenger_count: Mapped[int] = mapped_column(Integer)
    seat_class: Mapped[SeatClass] = mapped_column(Enum(SeatClass, native_enum=False))
    status: Mapped[SeatObservationStatus] = mapped_column(
        Enum(SeatObservationStatus, native_enum=False)
    )
    source: Mapped[str] = mapped_column(String(80))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fresh_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
