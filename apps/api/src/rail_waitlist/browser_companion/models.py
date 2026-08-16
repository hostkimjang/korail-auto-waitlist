from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base
from ..domain import SeatClass, SeatObservationStatus


def utcnow() -> datetime:
    return datetime.now(UTC)


class KorailBrowserSnapshotBatch(Base):
    __tablename__ = "korail_browser_snapshot_batches"
    __table_args__ = (
        CheckConstraint(
            "source = 'korail-official-browser-companion'",
            name="ck_korail_browser_snapshot_batch_source",
        ),
        CheckConstraint(
            "passenger_count BETWEEN 1 AND 9",
            name="ck_korail_browser_snapshot_batch_passenger_count",
        ),
        CheckConstraint(
            "fresh_until > observed_at",
            name="ck_korail_browser_snapshot_batch_freshness_order",
        ),
        Index(
            "ix_korail_browser_snapshot_batch_route_fresh",
            "origin",
            "destination",
            "travel_date",
            "passenger_count",
            "fresh_until",
            "observed_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    credential_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("browser_companion_credentials.id", ondelete="SET NULL"),
        nullable=True,
    )
    challenge_id: Mapped[str | None] = mapped_column(String(36), unique=True, nullable=True)
    origin: Mapped[str] = mapped_column(String(40))
    destination: Mapped[str] = mapped_column(String(40))
    travel_date: Mapped[date] = mapped_column(Date)
    passenger_count: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(80))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fresh_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    snapshots: Mapped[list[KorailBrowserSeatSnapshot]] = relationship(
        back_populates="batch", cascade="all, delete-orphan", passive_deletes=True
    )


class KorailBrowserSeatSnapshot(Base):
    __tablename__ = "korail_browser_seat_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "train_number",
            "seat_class",
            name="uq_korail_browser_snapshot_batch_train_seat",
        ),
        CheckConstraint(
            "seat_class IN ('STANDARD', 'FIRST')",
            name="ck_korail_browser_snapshot_seat_class",
        ),
        CheckConstraint(
            "status IN ('AVAILABLE', 'LIMITED', 'STANDING_PLUS_SEAT', 'STANDING_ONLY', "
            "'SOLD_OUT', 'WAITLIST_AVAILABLE', 'NOT_OFFERED')",
            name="ck_korail_browser_snapshot_status",
        ),
        Index(
            "ix_korail_browser_snapshot_identity",
            "train_number",
            "departure_at",
            "seat_class",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    batch_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("korail_browser_snapshot_batches.id", ondelete="CASCADE")
    )
    train_number: Mapped[str] = mapped_column(String(40))
    departure_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    seat_class: Mapped[SeatClass] = mapped_column(Enum(SeatClass, native_enum=False))
    status: Mapped[SeatObservationStatus] = mapped_column(
        Enum(SeatObservationStatus, native_enum=False)
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    batch: Mapped[KorailBrowserSnapshotBatch] = relationship(back_populates="snapshots")


class BrowserCompanionPairing(Base):
    __tablename__ = "browser_companion_pairings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(80))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BrowserCompanionCredential(Base):
    __tablename__ = "browser_companion_credentials"
    __table_args__ = (
        Index(
            "ix_browser_companion_credential_installation",
            "extension_origin",
            "client_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    extension_origin: Mapped[str] = mapped_column(String(100))
    client_id: Mapped[str] = mapped_column(String(36))
    label: Mapped[str] = mapped_column(String(80))
    window_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    accepted_in_window: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BrowserCompanionChallenge(Base):
    __tablename__ = "browser_companion_challenges"
    __table_args__ = (
        Index(
            "ix_browser_companion_challenge_active",
            "credential_id",
            "expires_at",
            "consumed_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    credential_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("browser_companion_credentials.id", ondelete="CASCADE"),
    )
    challenge_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(160))
    body_sha256: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
