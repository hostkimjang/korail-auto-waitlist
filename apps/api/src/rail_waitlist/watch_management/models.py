from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time

from sqlalchemy import (
    JSON,
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
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base
from ..domain import (
    BookingWindowStatus,
    OperationalStatus,
    Provider,
    ReservationOutcome,
    ReservationPolicy,
    SeatObservationMode,
    SeatObservationStatus,
    WatchStatus,
)
from ..reservations.provider_confirmation.contracts import ReservationConfirmationOutcome
from ..timetable_management.models import TimetableSeatEvidence


def utcnow() -> datetime:
    return datetime.now(UTC)


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
    observation_in_flight_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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
    candidates: Mapped[list[WatchCandidate]] = relationship(
        back_populates="watch",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="WatchCandidate.priority",
    )
    transition_history: Mapped[list[WatchTransitionHistory]] = relationship(
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
        CheckConstraint(
            "(manual_rearm_source_attempt_id IS NULL AND manual_rearm_authorized_at IS NULL) OR "
            "(manual_rearm_source_attempt_id IS NOT NULL "
            "AND manual_rearm_authorized_at IS NOT NULL)",
            name="ck_watch_candidate_manual_rearm_shape",
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
    manual_rearm_source_attempt_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    manual_rearm_authorized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    watch: Mapped[Watch] = relationship(back_populates="candidates")
    registration_evidence: Mapped[TimetableSeatEvidence | None] = relationship(lazy="joined")
    suppressed_by_candidate: Mapped[WatchCandidate | None] = relationship(
        remote_side=[id],
        foreign_keys=[suppressed_by_candidate_id],
        back_populates="suppressed_candidates",
    )
    suppressed_candidates: Mapped[list[WatchCandidate]] = relationship(
        foreign_keys=[suppressed_by_candidate_id],
        back_populates="suppressed_by_candidate",
        passive_deletes=True,
    )
    observations: Mapped[list[SeatObservation]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="SeatObservation.observed_at",
    )
    reservation_attempts: Mapped[list[ReservationAttempt]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by=lambda: (
            ReservationAttempt.attempt_sequence,
            ReservationAttempt.started_at,
        ),
    )

    @property
    def reservation_attempt(self) -> ReservationAttempt | None:
        """Return the latest attempt for legacy read sites during the sequence migration."""
        return self.reservation_attempts[-1] if self.reservation_attempts else None

    @reservation_attempt.setter
    def reservation_attempt(self, value: ReservationAttempt | None) -> None:
        self.reservation_attempts.clear()
        if value is not None:
            self.reservation_attempts.append(value)

    @property
    def train_type(self) -> str | None:
        """Expose only the subtype captured by immutable registration evidence."""
        return (
            self.registration_evidence.train_type
            if self.registration_evidence is not None
            else None
        )


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
    transition_history: Mapped[list[WatchTransitionHistory]] = relationship(
        back_populates="observation", passive_deletes=True
    )


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
    progress_stages: Mapped[list[dict[str, str]]] = mapped_column(
        JSON,
        default=list,
        server_default="[]",
    )
    reserved_seats: Mapped[list[dict[str, str]]] = mapped_column(
        JSON,
        default=list,
        server_default="[]",
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
