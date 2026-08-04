from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base
from .domain import (
    BookingWindowStatus,
    NotificationKind,
    OperationalStatus,
    OutboxStatus,
    Provider,
    ProviderCircuitState,
    ReservationOutcome,
    ReservationPolicy,
    SeatClass,
    SeatObservationMode,
    SeatObservationStatus,
    WatchStatus,
)
from .reservation_confirmation import ReservationConfirmationOutcome


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Watch(Base):
    __tablename__ = "watches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    provider: Mapped[Provider] = mapped_column(Enum(Provider, native_enum=False), index=True)
    origin: Mapped[str] = mapped_column(String(40))
    origin_node_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    destination: Mapped[str] = mapped_column(String(40))
    destination_node_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    travel_date: Mapped[date] = mapped_column(Date)
    time_from: Mapped[time] = mapped_column(Time)
    time_to: Mapped[time] = mapped_column(Time)
    seat_class: Mapped[str] = mapped_column(String(20), default="standard")
    passenger_count: Mapped[int] = mapped_column(Integer, default=1)
    train_numbers: Mapped[list[str]] = mapped_column(JSON, default=list)
    notification_channel_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    mode: Mapped[str] = mapped_column(String(20), default="official")
    reservation_policy: Mapped[ReservationPolicy] = mapped_column(
        Enum(ReservationPolicy, native_enum=False),
        default=ReservationPolicy.NOTIFY_ONLY,
        server_default=ReservationPolicy.NOTIFY_ONLY.name,
    )
    seat_observation_mode: Mapped[SeatObservationMode] = mapped_column(
        Enum(SeatObservationMode, native_enum=False),
        default=SeatObservationMode.BALANCED,
        server_default=SeatObservationMode.BALANCED.name,
    )
    focused_observation_interval_seconds: Mapped[int] = mapped_column(
        Integer,
        default=25,
        server_default="25",
    )
    status: Mapped[WatchStatus] = mapped_column(
        Enum(WatchStatus, native_enum=False), default=WatchStatus.DRAFT, index=True
    )
    dedupe_key: Mapped[str] = mapped_column(String(64), index=True)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reservation_attempted: Mapped[bool] = mapped_column(Boolean, default=False)
    unchanged_runs: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    official_booking_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    candidates: Mapped[list["WatchCandidate"]] = relationship(
        back_populates="watch",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="WatchCandidate.priority",
    )
    transition_history: Mapped[list["WatchTransitionHistory"]] = relationship(
        back_populates="watch",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="WatchTransitionHistory.created_at",
    )

    __table_args__ = (
        CheckConstraint("unchanged_runs >= 0", name="ck_watch_unchanged_runs_nonnegative"),
        CheckConstraint(
            "reservation_policy IN ('NOTIFY_ONLY', 'RESERVE_ONCE_BEFORE_PAYMENT')",
            name="ck_watch_reservation_policy_allowed",
        ),
        CheckConstraint(
            "seat_observation_mode IN ('BALANCED', 'FOCUSED')",
            name="ck_watch_seat_observation_mode_allowed",
        ),
        CheckConstraint(
            "focused_observation_interval_seconds BETWEEN 20 AND 30",
            name="ck_watch_focused_observation_interval_seconds",
        ),
    )


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


class WatchCandidate(Base):
    __tablename__ = "watch_candidates"
    __table_args__ = (
        UniqueConstraint(
            "watch_id",
            "train_number",
            "departure_at",
            "seat_class",
            name="uq_watch_candidate_identity",
        ),
        UniqueConstraint("watch_id", "priority", name="uq_watch_candidate_priority"),
        CheckConstraint("priority >= 1", name="ck_watch_candidate_priority_positive"),
        CheckConstraint(
            "delay_minutes IS NULL OR delay_minutes >= 0",
            name="ck_watch_candidate_delay_minutes_nonnegative",
        ),
        CheckConstraint(
            "operational_source IS NULL OR "
            "(operational_observed_at IS NOT NULL AND operational_fresh_until IS NOT NULL "
            "AND operational_fresh_until >= operational_observed_at)",
            name="ck_watch_candidate_operational_provenance_shape",
        ),
        CheckConstraint(
            "operational_source IS NOT NULL OR "
            "(operational_observed_at IS NULL AND operational_fresh_until IS NULL)",
            name="ck_watch_candidate_operational_provenance_absent_shape",
        ),
        CheckConstraint(
            "operational_source IS NULL OR length(trim(operational_source)) > 0",
            name="ck_watch_candidate_operational_source_nonempty",
        ),
        CheckConstraint(
            "state IN ('active', 'observed', 'seat_found', 'reservation_attempted', "
            "'payment_required', 'suppressed_by_priority', 'expired', 'failed')",
            name="ck_watch_candidate_state_allowed",
        ),
        CheckConstraint(
            "suppressed_by_candidate_id IS NULL OR suppressed_by_candidate_id <> id",
            name="ck_watch_candidate_not_self_suppressed",
        ),
        Index("ix_watch_candidates_watch_state", "watch_id", "state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    watch_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("watches.id", ondelete="CASCADE"), index=True
    )
    train_number: Mapped[str] = mapped_column(String(40))
    departure_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # departure_at remains the immutable candidate identity used by the existing unique key.
    scheduled_departure_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda context: context.get_current_parameters()["departure_at"],
    )
    estimated_departure_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    actual_departure_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delay_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    operational_status: Mapped[OperationalStatus] = mapped_column(
        Enum(OperationalStatus, native_enum=False),
        default=OperationalStatus.UNKNOWN,
        server_default=OperationalStatus.UNKNOWN.name,
    )
    booking_window_status: Mapped[BookingWindowStatus] = mapped_column(
        Enum(BookingWindowStatus, native_enum=False),
        default=BookingWindowStatus.UNKNOWN,
        server_default=BookingWindowStatus.UNKNOWN.name,
    )
    operational_source: Mapped[str | None] = mapped_column(String(80), nullable=True)
    operational_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    operational_fresh_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    arrival_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    seat_class: Mapped[str] = mapped_column(String(20))
    priority: Mapped[int] = mapped_column(Integer)
    registration_evidence_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("timetable_seat_evidence.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    state: Mapped[str] = mapped_column(String(32), default="active", server_default="active")
    suppressed_by_candidate_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("watch_candidates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    watch: Mapped[Watch] = relationship(back_populates="candidates")
    registration_evidence: Mapped[TimetableSeatEvidence | None] = relationship(lazy="joined")
    suppressed_by_candidate: Mapped["WatchCandidate | None"] = relationship(
        remote_side=[id],
        foreign_keys=[suppressed_by_candidate_id],
        back_populates="suppressed_candidates",
    )
    suppressed_candidates: Mapped[list["WatchCandidate"]] = relationship(
        foreign_keys=[suppressed_by_candidate_id],
        back_populates="suppressed_by_candidate",
        passive_deletes=True,
    )
    observations: Mapped[list["SeatObservation"]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="SeatObservation.observed_at",
    )
    reservation_attempts: Mapped[list["ReservationAttempt"]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by=lambda: (
            ReservationAttempt.attempt_sequence,
            ReservationAttempt.started_at,
        ),
    )

    @property
    def reservation_attempt(self) -> "ReservationAttempt | None":
        """Return the latest attempt for legacy read sites during the sequence migration."""
        return self.reservation_attempts[-1] if self.reservation_attempts else None

    @reservation_attempt.setter
    def reservation_attempt(self, value: "ReservationAttempt | None") -> None:
        self.reservation_attempts.clear()
        if value is not None:
            self.reservation_attempts.append(value)


class SeatObservation(Base):
    __tablename__ = "seat_observations"
    __table_args__ = (
        CheckConstraint("length(trim(source)) > 0", name="ck_seat_observation_source_nonempty"),
        CheckConstraint("fresh_until >= observed_at", name="ck_seat_observation_freshness_order"),
        CheckConstraint(
            "error_category IS NULL OR length(trim(error_category)) > 0",
            name="ck_seat_observation_error_category_nonempty",
        ),
        CheckConstraint(
            "status IN ('UNAVAILABLE', 'UNKNOWN', 'AVAILABLE', 'LIMITED', "
            "'STANDING_PLUS_SEAT', 'NOT_ENOUGH_SEATS', 'SOLD_OUT', "
            "'WAITLIST_AVAILABLE', 'RESERVATION_COMPLETED', 'NOT_OFFERED', "
            "'DEPARTED', 'OUT_OF_SERVICE', 'STALE', 'ERROR')",
            name="ck_seat_observation_status_allowed",
        ),
        Index("ix_seat_observations_candidate_observed_at", "candidate_id", "observed_at"),
        Index("ix_seat_observations_observed_at", "observed_at"),
        Index("ix_seat_observations_status_fresh_until", "status", "fresh_until"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    candidate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("watch_candidates.id", ondelete="CASCADE")
    )
    status: Mapped[SeatObservationStatus] = mapped_column(
        Enum(SeatObservationStatus, native_enum=False)
    )
    source: Mapped[str] = mapped_column(String(80))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fresh_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    error_category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    candidate: Mapped[WatchCandidate] = relationship(back_populates="observations")
    transition_history: Mapped[list["WatchTransitionHistory"]] = relationship(
        back_populates="observation", passive_deletes=True
    )


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
    snapshots: Mapped[list["KorailBrowserSeatSnapshot"]] = relationship(
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
            "status IN ('AVAILABLE', 'LIMITED', 'STANDING_PLUS_SEAT', 'SOLD_OUT', "
            "'WAITLIST_AVAILABLE', 'NOT_OFFERED')",
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


class ReservationAttempt(Base):
    __tablename__ = "reservation_attempts"
    __table_args__ = (
        CheckConstraint(
            "length(trim(idempotency_key)) > 0",
            name="ck_reservation_attempt_idempotency_key_nonempty",
        ),
        CheckConstraint(
            "length(trim(episode_key)) > 0",
            name="ck_reservation_attempt_episode_key_nonempty",
        ),
        CheckConstraint(
            "attempt_sequence >= 1",
            name="ck_reservation_attempt_sequence_positive",
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_reservation_attempt_timestamp_order",
        ),
        CheckConstraint(
            "official_handoff_url IS NULL OR official_handoff_url LIKE 'https://%'",
            name="ck_reservation_attempt_handoff_https",
        ),
        CheckConstraint(
            "outcome IN ('PENDING', 'PAYMENT_REQUIRED', 'RESERVED', 'NOT_AVAILABLE', "
            "'AUTH_REQUIRED', 'PROVIDER_BLOCKED', 'FAILED', 'UNKNOWN')",
            name="ck_reservation_attempt_outcome_allowed",
        ),
        CheckConstraint(
            "credential_version IS NULL OR credential_version >= 1",
            name="ck_reservation_attempt_credential_version_positive",
        ),
        CheckConstraint(
            "confirmation_source IS NULL OR length(trim(confirmation_source)) > 0",
            name="ck_reservation_attempt_confirmation_source_nonempty",
        ),
        CheckConstraint(
            "(confirmation_outcome IS NULL AND confirmation_source IS NULL "
            "AND confirmation_observed_at IS NULL) OR "
            "(confirmation_outcome IS NOT NULL AND confirmation_source IS NOT NULL "
            "AND confirmation_observed_at IS NOT NULL)",
            name="ck_reservation_attempt_confirmation_provenance_shape",
        ),
        CheckConstraint(
            "last_reconciled_at IS NULL OR (confirmation_observed_at IS NOT NULL "
            "AND last_reconciled_at >= confirmation_observed_at)",
            name="ck_reservation_attempt_reconciliation_timestamp_order",
        ),
        CheckConstraint(
            "reconciliation_attempt_count >= 0 AND reconciliation_attempt_count <= 6",
            name="ck_reservation_attempt_reconciliation_attempt_count_bounded",
        ),
        Index("ix_reservation_attempts_started_at", "started_at"),
        Index("ix_reservation_attempts_outcome_started_at", "outcome", "started_at"),
        Index("ix_reservation_attempts_next_reconcile_at", "next_reconcile_at"),
        Index(
            "ix_reservation_attempts_post_deadline_reconciled_at",
            "post_deadline_reconciled_at",
        ),
        UniqueConstraint(
            "candidate_id",
            "attempt_sequence",
            name="uq_reservation_attempt_candidate_sequence",
        ),
        UniqueConstraint(
            "candidate_id",
            "episode_key",
            name="uq_reservation_attempt_candidate_episode",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    candidate_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("watch_candidates.id", ondelete="CASCADE"),
    )
    attempt_sequence: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    episode_key: Mapped[str] = mapped_column(
        String(128),
        default=lambda: f"legacy:{uuid.uuid4()}",
        server_default="legacy",
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[ReservationOutcome] = mapped_column(
        Enum(ReservationOutcome, native_enum=False), default=ReservationOutcome.PENDING
    )
    payment_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    official_handoff_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    credential_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmation_outcome: Mapped[ReservationConfirmationOutcome | None] = mapped_column(
        Enum(ReservationConfirmationOutcome, native_enum=False), nullable=True
    )
    confirmation_source: Mapped[str | None] = mapped_column(String(80), nullable=True)
    confirmation_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reconciliation_attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    next_reconcile_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    post_deadline_reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    candidate: Mapped[WatchCandidate] = relationship(back_populates="reservation_attempts")


class WatchTransitionHistory(Base):
    __tablename__ = "watch_transition_history"
    __table_args__ = (
        CheckConstraint("from_status <> to_status", name="ck_watch_transition_status_changed"),
        CheckConstraint("length(trim(reason)) > 0", name="ck_watch_transition_reason_nonempty"),
        Index("ix_watch_transition_history_watch_created_at", "watch_id", "created_at"),
        Index("ix_watch_transition_history_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    watch_id: Mapped[str] = mapped_column(String(36), ForeignKey("watches.id", ondelete="CASCADE"))
    from_status: Mapped[WatchStatus] = mapped_column(Enum(WatchStatus, native_enum=False))
    to_status: Mapped[WatchStatus] = mapped_column(Enum(WatchStatus, native_enum=False))
    reason: Mapped[str] = mapped_column(String(160))
    observation_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("seat_observations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    watch: Mapped[Watch] = relationship(back_populates="transition_history")
    observation: Mapped[SeatObservation | None] = relationship(back_populates="transition_history")


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

    provider: Mapped[Provider] = mapped_column(
        Enum(Provider, native_enum=False), primary_key=True
    )
    account_scope: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
    schema_version: Mapped[int] = mapped_column(Integer, default=2, server_default="2")
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


class NotificationChannel(Base):
    __tablename__ = "notification_channels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    kind: Mapped[NotificationKind] = mapped_column(Enum(NotificationKind, native_enum=False))
    name: Mapped[str] = mapped_column(String(80))
    config_ciphertext: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (Index("ix_outbox_events_processed_at", "processed_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    aggregate_type: Mapped[str] = mapped_column(String(40))
    aggregate_id: Mapped[str] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    dedupe_key: Mapped[str] = mapped_column(String(128), unique=True)
    status: Mapped[OutboxStatus] = mapped_column(
        Enum(OutboxStatus, native_enum=False), default=OutboxStatus.PENDING, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(240), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("scope", "key", name="uq_idempotency_scope_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    scope: Mapped[str] = mapped_column(String(100))
    key: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[str] = mapped_column(String(36))
    request_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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


class AdminAccount(Base):
    __tablename__ = "admin_accounts"
    __table_args__ = (
        CheckConstraint("singleton_slot = 1", name="ck_admin_account_singleton_slot"),
        CheckConstraint("length(trim(username)) >= 3", name="ck_admin_account_username_nonempty"),
        CheckConstraint(
            "timetable_refresh_interval_seconds BETWEEN 5 AND 300",
            name="ck_admin_account_timetable_refresh_interval_seconds",
        ),
        CheckConstraint(
            "observation_interval_seconds BETWEEN 1 AND 600",
            name="ck_admin_account_observation_interval_seconds",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    singleton_slot: Mapped[int] = mapped_column(Integer, unique=True, default=1)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    timetable_refresh_interval_seconds: Mapped[int] = mapped_column(
        Integer,
        default=5,
        server_default="5",
    )
    observation_interval_seconds: Mapped[int] = mapped_column(
        Integer,
        default=5,
        server_default="5",
    )
    preferences_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AdminSession(Base):
    __tablename__ = "admin_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
