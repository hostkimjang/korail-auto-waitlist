from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Index,
    Integer,
    String,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..domain import Provider, SeatClass, SeatObservationStatus


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimetableSeatEvidence(Base):
    __tablename__ = "timetable_seat_evidence"
    __table_args__ = (
        UniqueConstraint("evidence_hash", name="uq_timetable_seat_evidence_hash"),
        CheckConstraint(
            "provider IN ('KORAIL', 'SRT')", name="ck_timetable_seat_evidence_provider"
        ),
        CheckConstraint(
            "seat_class IN ('STANDARD', 'FIRST')",
            name="ck_timetable_seat_evidence_seat_class",
        ),
        CheckConstraint(
            "passenger_count BETWEEN 1 AND 9",
            name="ck_timetable_seat_evidence_passenger_count",
        ),
        CheckConstraint(
            "provenance_kind IN ('not_observed', 'official_provider', "
            "'official_page_browser_companion', 'user_confirmed_official_page')",
            name="ck_timetable_seat_evidence_provenance_kind",
        ),
        CheckConstraint(
            "((provenance_kind = 'not_observed' AND status = 'UNKNOWN' "
            "AND reason IS NOT NULL AND source IS NULL AND observed_at IS NULL "
            "AND fresh_until IS NULL) OR "
            "(provenance_kind <> 'not_observed' AND source IS NOT NULL "
            "AND observed_at IS NOT NULL AND reason IS NULL))",
            name="ck_timetable_seat_evidence_provenance_shape",
        ),
        CheckConstraint(
            "(provenance_kind <> 'user_confirmed_official_page' OR "
            "(source = 'official-page-user-confirmation' "
            "AND fresh_until IS NOT NULL AND fresh_until > observed_at))",
            name="ck_timetable_seat_evidence_user_confirmation",
        ),
        CheckConstraint(
            "(provenance_kind <> 'official_page_browser_companion' OR "
            "(source = 'korail-official-browser-companion' "
            "AND fresh_until IS NOT NULL AND fresh_until > observed_at))",
            name="ck_timetable_seat_evidence_browser_companion",
        ),
        CheckConstraint(
            "registration_valid_until > created_at",
            name="ck_timetable_seat_evidence_registration_window",
        ),
        Index(
            "ix_timetable_seat_evidence_identity",
            "provider",
            "origin_node_id",
            "destination_node_id",
            "departure_at",
            "passenger_count",
            "seat_class",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    evidence_hash: Mapped[str] = mapped_column(String(64))
    provider: Mapped[Provider] = mapped_column(Enum(Provider, native_enum=False))
    origin_node_id: Mapped[str] = mapped_column(String(80))
    destination_node_id: Mapped[str] = mapped_column(String(80))
    canonical_train_number: Mapped[str] = mapped_column(String(40))
    train_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    departure_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    passenger_count: Mapped[int] = mapped_column(Integer)
    seat_class: Mapped[SeatClass] = mapped_column(Enum(SeatClass, native_enum=False))
    status: Mapped[SeatObservationStatus] = mapped_column(
        Enum(SeatObservationStatus, native_enum=False)
    )
    provenance_kind: Mapped[str] = mapped_column(String(40))
    source: Mapped[str | None] = mapped_column(String(80), nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fresh_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    registration_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    registration_valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    @property
    def provenance(self) -> dict[str, Any]:
        def aware(value: datetime | None) -> datetime | None:
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                return value.replace(tzinfo=timezone.utc)
            return value

        return {
            "kind": self.provenance_kind,
            "source": self.source,
            "observed_at": aware(self.observed_at),
            "fresh_until": aware(self.fresh_until),
            "reason": self.reason,
        }


class StationCatalogCache(Base):
    __tablename__ = "station_catalog_cache"
    __table_args__ = (
        CheckConstraint(
            "cache_key = 'tago_station_catalog_all'",
            name="ck_station_catalog_cache_canonical_key",
        ),
        CheckConstraint(
            "schema_version >= 1",
            name="ck_station_catalog_cache_schema_version_positive",
        ),
        CheckConstraint(
            "station_count >= 0",
            name="ck_station_catalog_cache_count_nonnegative",
        ),
        CheckConstraint(
            "payload IS NULL OR station_count > 0",
            name="ck_station_catalog_cache_payload_nonempty",
        ),
        CheckConstraint(
            "payload IS NULL OR (retrieved_at IS NOT NULL AND refresh_after IS NOT NULL)",
            name="ck_station_catalog_cache_payload_timestamps",
        ),
        CheckConstraint(
            "refresh_owner IS NULL OR length(trim(refresh_owner)) > 0",
            name="ck_station_catalog_cache_owner_nonempty",
        ),
        CheckConstraint(
            "last_error_category IS NULL OR length(trim(last_error_category)) > 0",
            name="ck_station_catalog_cache_error_nonempty",
        ),
    )

    cache_key: Mapped[str] = mapped_column(String(40), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=4, server_default="4")
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    station_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refresh_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refresh_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
