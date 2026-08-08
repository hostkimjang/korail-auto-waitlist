from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..domain import NotificationKind


def utcnow() -> datetime:
    return datetime.now(UTC)


class NotificationChannel(Base):
    __tablename__ = "notification_channels"
    __table_args__ = (
        Index(
            "ix_notification_channels_web_push_device_key",
            "web_push_device_key",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    kind: Mapped[NotificationKind] = mapped_column(Enum(NotificationKind, native_enum=False))
    name: Mapped[str] = mapped_column(String(80))
    config_ciphertext: Mapped[str] = mapped_column(Text)
    web_push_device_key: Mapped[str | None] = mapped_column(String(43), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class NativePushPairing(Base):
    """Retired pairing rows retained for migration and data-preservation compatibility."""

    __tablename__ = "native_push_pairings"
    __table_args__ = (
        Index("ix_native_push_pairings_code_hash", "code_hash", unique=True),
        Index("ix_native_push_pairings_expires_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    code_hash: Mapped[str] = mapped_column(String(64))
    kind: Mapped[NotificationKind] = mapped_column(Enum(NotificationKind, native_enum=False))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NativePushCredential(Base):
    """Retired device credentials retained without re-enabling native delivery."""

    __tablename__ = "native_push_credentials"
    __table_args__ = (
        Index("ix_native_push_credentials_token_hash", "token_hash", unique=True),
        Index("ix_native_push_credentials_channel_id", "channel_id", unique=True),
        Index("ix_native_push_credentials_revoked_at", "revoked_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    token_hash: Mapped[str] = mapped_column(String(64))
    channel_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("notification_channels.id", ondelete="RESTRICT"),
    )
    kind: Mapped[NotificationKind] = mapped_column(Enum(NotificationKind, native_enum=False))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
