from __future__ import annotations

import json
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint, DateTime, Enum, UniqueConstraint
from sqlalchemy.orm import configure_mappers

import rail_waitlist.models as legacy
from rail_waitlist.database import Base
from rail_waitlist.domain import (
    BookingWindowStatus,
    OperationalStatus,
    Provider,
    ReservationOutcome,
    ReservationPolicy,
    ReservationResultReasonCode,
    SeatObservationMode,
    SeatObservationStatus,
    WatchStatus,
)
from rail_waitlist.reservation_confirmation import (
    ReservationConfirmationDiagnosticCode,
    ReservationConfirmationOutcome,
)
from rail_waitlist.reservations.reconciliation_policy import (
    ReservationReconciliationResolution,
)
from rail_waitlist.timetable_management.models import TimetableSeatEvidence
from rail_waitlist.watch_management import models as canonical

API_ROOT = Path(__file__).resolve().parents[1]
MODEL_NAMES = (
    "Watch",
    "WatchCandidate",
    "SeatObservation",
    "ReservationAttempt",
    "WatchTransitionHistory",
)
COLUMN_ORDERS = {
    "Watch": (
        "id",
        "provider",
        "origin",
        "origin_node_id",
        "destination",
        "destination_node_id",
        "travel_date",
        "time_from",
        "time_to",
        "seat_class",
        "passenger_count",
        "train_numbers",
        "notification_channel_ids",
        "mode",
        "reservation_policy",
        "seat_observation_mode",
        "focused_observation_interval_seconds",
        "status",
        "dedupe_key",
        "next_check_at",
        "observation_in_flight_until",
        "cooldown_until",
        "payment_deadline",
        "reservation_attempted",
        "unchanged_runs",
        "official_booking_url",
        "created_at",
        "updated_at",
    ),
    "WatchCandidate": (
        "id",
        "watch_id",
        "train_number",
        "departure_at",
        "scheduled_departure_at",
        "estimated_departure_at",
        "actual_departure_at",
        "delay_minutes",
        "operational_status",
        "booking_window_status",
        "operational_source",
        "operational_observed_at",
        "operational_fresh_until",
        "arrival_at",
        "seat_class",
        "priority",
        "registration_evidence_id",
        "state",
        "suppressed_by_candidate_id",
        "manual_rearm_source_attempt_id",
        "manual_rearm_authorized_at",
    ),
    "SeatObservation": (
        "id",
        "candidate_id",
        "status",
        "source",
        "observed_at",
        "fresh_until",
        "error_category",
    ),
    "ReservationAttempt": (
        "id",
        "candidate_id",
        "attempt_sequence",
        "episode_key",
        "idempotency_key",
        "started_at",
        "finished_at",
        "outcome",
        "result_reason_code",
        "payment_deadline",
        "official_handoff_url",
        "credential_version",
        "confirmation_outcome",
        "confirmation_diagnostic_code",
        "confirmation_source",
        "confirmation_observed_at",
        "last_reconciled_at",
        "reconciliation_attempt_count",
        "next_reconcile_at",
        "reconciliation_resolution",
        "post_deadline_reconciled_at",
        "progress_stages",
        "reserved_seats",
        "confirmation_correlation_seats",
    ),
    "WatchTransitionHistory": (
        "id",
        "watch_id",
        "from_status",
        "to_status",
        "reason",
        "observation_id",
        "created_at",
    ),
}
NULLABLE_COLUMNS = {
    "Watch": {
        "origin_node_id",
        "destination_node_id",
        "next_check_at",
        "observation_in_flight_until",
        "cooldown_until",
        "payment_deadline",
        "official_booking_url",
    },
    "WatchCandidate": {
        "estimated_departure_at",
        "actual_departure_at",
        "delay_minutes",
        "operational_source",
        "operational_observed_at",
        "operational_fresh_until",
        "arrival_at",
        "registration_evidence_id",
        "suppressed_by_candidate_id",
        "manual_rearm_source_attempt_id",
        "manual_rearm_authorized_at",
    },
    "SeatObservation": {"error_category"},
    "ReservationAttempt": {
        "finished_at",
        "payment_deadline",
        "official_handoff_url",
        "credential_version",
        "confirmation_outcome",
        "confirmation_diagnostic_code",
        "confirmation_source",
        "confirmation_observed_at",
        "last_reconciled_at",
        "next_reconcile_at",
        "reconciliation_resolution",
        "post_deadline_reconciled_at",
    },
    "WatchTransitionHistory": {"observation_id"},
}
INDEXES = {
    "Watch": {
        ("ix_watches_provider", False, ("provider",)),
        ("ix_watches_status", False, ("status",)),
        ("ix_watches_dedupe_key", False, ("dedupe_key",)),
    },
    "WatchCandidate": {
        ("ix_watch_candidates_watch_id", False, ("watch_id",)),
        (
            "ix_watch_candidates_registration_evidence_id",
            False,
            ("registration_evidence_id",),
        ),
        (
            "ix_watch_candidates_suppressed_by_candidate_id",
            False,
            ("suppressed_by_candidate_id",),
        ),
        ("ix_watch_candidates_watch_state", False, ("watch_id", "state")),
        (
            "ix_watch_candidates_manual_rearm_source_attempt_id",
            False,
            ("manual_rearm_source_attempt_id",),
        ),
    },
    "SeatObservation": {
        (
            "ix_seat_observations_candidate_observed_at",
            False,
            ("candidate_id", "observed_at"),
        ),
        ("ix_seat_observations_observed_at", False, ("observed_at",)),
        (
            "ix_seat_observations_status_fresh_until",
            False,
            ("status", "fresh_until"),
        ),
    },
    "ReservationAttempt": {
        ("ix_reservation_attempts_started_at", False, ("started_at",)),
        (
            "ix_reservation_attempts_outcome_started_at",
            False,
            ("outcome", "started_at"),
        ),
        ("ix_reservation_attempts_next_reconcile_at", False, ("next_reconcile_at",)),
        (
            "ix_reservation_attempts_post_deadline_reconciled_at",
            False,
            ("post_deadline_reconciled_at",),
        ),
    },
    "WatchTransitionHistory": {
        (
            "ix_watch_transition_history_watch_created_at",
            False,
            ("watch_id", "created_at"),
        ),
        ("ix_watch_transition_history_created_at", False, ("created_at",)),
        ("ix_watch_transition_history_observation_id", False, ("observation_id",)),
    },
}
UNIQUE_CONSTRAINTS = {
    "Watch": set(),
    "WatchCandidate": {
        (
            "uq_watch_candidate_identity",
            ("watch_id", "train_number", "departure_at", "seat_class"),
        ),
        ("uq_watch_candidate_priority", ("watch_id", "priority")),
    },
    "SeatObservation": set(),
    "ReservationAttempt": {
        (None, ("idempotency_key",)),
        (
            "uq_reservation_attempt_candidate_sequence",
            ("candidate_id", "attempt_sequence"),
        ),
        ("uq_reservation_attempt_candidate_episode", ("candidate_id", "episode_key")),
    },
    "WatchTransitionHistory": set(),
}
CHECK_CONSTRAINTS = {
    "Watch": {
        "ck_watch_focused_observation_interval_seconds": (
            "focused_observation_interval_seconds BETWEEN 20 AND 30"
        ),
        "ck_watch_reservation_policy_allowed": (
            "reservation_policy IN ('NOTIFY_ONLY', 'RESERVE_ONCE_BEFORE_PAYMENT')"
        ),
        "ck_watch_seat_observation_mode_allowed": (
            "seat_observation_mode IN ('BALANCED', 'FOCUSED')"
        ),
        "ck_watch_unchanged_runs_nonnegative": "unchanged_runs >= 0",
    },
    "WatchCandidate": {
        "ck_watch_candidate_delay_minutes_nonnegative": (
            "delay_minutes IS NULL OR delay_minutes >= 0"
        ),
        "ck_watch_candidate_not_self_suppressed": (
            "suppressed_by_candidate_id IS NULL OR suppressed_by_candidate_id <> id"
        ),
        "ck_watch_candidate_manual_rearm_shape": (
            "(manual_rearm_source_attempt_id IS NULL AND "
            "manual_rearm_authorized_at IS NULL) OR "
            "(manual_rearm_source_attempt_id IS NOT NULL AND "
            "manual_rearm_authorized_at IS NOT NULL)"
        ),
        "ck_watch_candidate_operational_provenance_absent_shape": (
            "operational_source IS NOT NULL OR "
            "(operational_observed_at IS NULL AND operational_fresh_until IS NULL)"
        ),
        "ck_watch_candidate_operational_provenance_shape": (
            "operational_source IS NULL OR (operational_observed_at IS NOT NULL AND "
            "operational_fresh_until IS NOT NULL AND "
            "operational_fresh_until >= operational_observed_at)"
        ),
        "ck_watch_candidate_operational_source_nonempty": (
            "operational_source IS NULL OR length(trim(operational_source)) > 0"
        ),
        "ck_watch_candidate_priority_positive": "priority >= 1",
        "ck_watch_candidate_state_allowed": (
            "state IN ('active', 'observed', 'seat_found', 'reservation_attempted', "
            "'payment_required', 'suppressed_by_priority', 'expired', 'failed')"
        ),
    },
    "SeatObservation": {
        "ck_seat_observation_error_category_nonempty": (
            "error_category IS NULL OR length(trim(error_category)) > 0"
        ),
        "ck_seat_observation_freshness_order": "fresh_until >= observed_at",
        "ck_seat_observation_source_nonempty": "length(trim(source)) > 0",
        "ck_seat_observation_status_allowed": (
            "status IN ('UNAVAILABLE', 'UNKNOWN', 'AVAILABLE', 'LIMITED', "
            "'STANDING_PLUS_SEAT', 'STANDING_ONLY', 'NOT_ENOUGH_SEATS', 'SOLD_OUT', "
            "'WAITLIST_AVAILABLE', 'RESERVATION_COMPLETED', 'NOT_OFFERED', "
            "'DEPARTED', 'OUT_OF_SERVICE', 'STALE', 'ERROR')"
        ),
    },
    "ReservationAttempt": {
        "ck_reservation_attempt_confirm_diag_allowed": (
            "confirmation_diagnostic_code IS NULL OR confirmation_diagnostic_code IN "
            "('OFFICIAL_READ_UNAVAILABLE', 'CREDENTIAL_CONTEXT_MISMATCH', "
            "'OFFICIAL_RECORD_AMBIGUOUS', 'OFFICIAL_EVIDENCE_INSUFFICIENT', 'UNSPECIFIED')"
        ),
        "ck_reservation_attempt_confirm_diag_inconclusive": (
            "confirmation_diagnostic_code IS NULL OR confirmation_outcome = 'INCONCLUSIVE'"
        ),
        "ck_reservation_attempt_confirmation_provenance_shape": (
            "(confirmation_outcome IS NULL AND confirmation_source IS NULL AND "
            "confirmation_observed_at IS NULL) OR (confirmation_outcome IS NOT NULL "
            "AND confirmation_source IS NOT NULL AND confirmation_observed_at IS NOT NULL)"
        ),
        "ck_reservation_attempt_confirmation_source_nonempty": (
            "confirmation_source IS NULL OR length(trim(confirmation_source)) > 0"
        ),
        "ck_reservation_attempt_credential_version_positive": (
            "credential_version IS NULL OR credential_version >= 1"
        ),
        "ck_reservation_attempt_episode_key_nonempty": "length(trim(episode_key)) > 0",
        "ck_reservation_attempt_handoff_https": (
            "official_handoff_url IS NULL OR official_handoff_url LIKE 'https://%'"
        ),
        "ck_reservation_attempt_idempotency_key_nonempty": ("length(trim(idempotency_key)) > 0"),
        "ck_reservation_attempt_outcome_allowed": (
            "outcome IN ('PENDING', 'PAYMENT_REQUIRED', 'RESERVED', 'NOT_AVAILABLE', "
            "'AUTH_REQUIRED', 'PROVIDER_BLOCKED', 'FAILED', 'UNKNOWN')"
        ),
        "ck_reservation_attempt_result_reason_code_allowed": (
            "result_reason_code IN ('RESERVATION_PENDING', 'PAYMENT_HOLD_CREATED', "
            "'TARGET_NOT_AVAILABLE', 'TARGET_AMBIGUOUS', 'SEAT_NOT_AVAILABLE', "
            "'RESERVATION_CONTROL_UNAVAILABLE', 'SEAT_SELECTION_LOST', "
            "'DELAY_CONSENT_REQUIRED', 'EXISTING_RESERVATION_ACTION_REQUIRED', "
            "'PROVIDER_NOTICE_ACTION_REQUIRED', 'AUTHENTICATION_REQUIRED', "
            "'PROVIDER_BLOCKED', 'PROVIDER_UNAVAILABLE', 'PROVIDER_RESPONSE_INVALID', "
            "'RESERVATION_REQUEST_RESULT_UNKNOWN', 'RESERVATION_FAILED')"
        ),
        "ck_reservation_attempt_reconciliation_attempt_count_bounded": (
            "reconciliation_attempt_count >= 0 AND reconciliation_attempt_count <= 6"
        ),
        "ck_reservation_attempt_reconciliation_timestamp_order": (
            "last_reconciled_at IS NULL OR (confirmation_observed_at IS NOT NULL AND "
            "last_reconciled_at >= confirmation_observed_at)"
        ),
        "ck_reservation_attempt_reconcile_resolution_allowed": (
            "reconciliation_resolution IS NULL OR reconciliation_resolution IN "
            "('CONFIRMED_ABSENT', 'EXHAUSTED_UNRESOLVED')"
        ),
        "ck_reservation_attempt_reconcile_resolution_shape": (
            "reconciliation_resolution IS NULL OR "
            "(reconciliation_resolution = 'CONFIRMED_ABSENT' "
            "AND outcome = 'UNKNOWN' AND confirmation_outcome = 'NOT_FOUND' "
            "AND confirmation_observed_at IS NOT NULL AND last_reconciled_at IS NOT NULL "
            "AND reconciliation_attempt_count >= 1 AND next_reconcile_at IS NULL) OR "
            "(reconciliation_resolution = 'EXHAUSTED_UNRESOLVED' "
            "AND outcome = 'UNKNOWN' "
            "AND confirmation_outcome IN ('INCONCLUSIVE', 'NOT_FOUND') "
            "AND confirmation_observed_at IS NOT NULL AND last_reconciled_at IS NOT NULL "
            "AND reconciliation_attempt_count >= 6 AND next_reconcile_at IS NULL)"
        ),
        "ck_reservation_attempt_sequence_positive": "attempt_sequence >= 1",
        "ck_reservation_attempt_timestamp_order": (
            "finished_at IS NULL OR finished_at >= started_at"
        ),
    },
    "WatchTransitionHistory": {
        "ck_watch_transition_reason_nonempty": "length(trim(reason)) > 0",
        "ck_watch_transition_status_changed": "from_status <> to_status",
    },
}
NON_ENUM_TYPE_SIGNATURES = {
    "Watch": {
        "id": ("String", 36, None),
        "origin": ("String", 40, None),
        "origin_node_id": ("String", 80, None),
        "destination": ("String", 40, None),
        "destination_node_id": ("String", 80, None),
        "travel_date": ("Date", None, None),
        "time_from": ("Time", None, False),
        "time_to": ("Time", None, False),
        "seat_class": ("String", 20, None),
        "passenger_count": ("Integer", None, None),
        "train_numbers": ("JSON", None, None),
        "notification_channel_ids": ("JSON", None, None),
        "mode": ("String", 20, None),
        "focused_observation_interval_seconds": ("Integer", None, None),
        "dedupe_key": ("String", 64, None),
        "next_check_at": ("DateTime", None, True),
        "observation_in_flight_until": ("DateTime", None, True),
        "cooldown_until": ("DateTime", None, True),
        "payment_deadline": ("DateTime", None, True),
        "reservation_attempted": ("Boolean", None, None),
        "unchanged_runs": ("Integer", None, None),
        "official_booking_url": ("Text", None, None),
        "created_at": ("DateTime", None, True),
        "updated_at": ("DateTime", None, True),
    },
    "WatchCandidate": {
        "id": ("String", 36, None),
        "watch_id": ("String", 36, None),
        "train_number": ("String", 40, None),
        "departure_at": ("DateTime", None, True),
        "scheduled_departure_at": ("DateTime", None, True),
        "estimated_departure_at": ("DateTime", None, True),
        "actual_departure_at": ("DateTime", None, True),
        "delay_minutes": ("Integer", None, None),
        "operational_source": ("String", 80, None),
        "operational_observed_at": ("DateTime", None, True),
        "operational_fresh_until": ("DateTime", None, True),
        "arrival_at": ("DateTime", None, True),
        "seat_class": ("String", 20, None),
        "priority": ("Integer", None, None),
        "registration_evidence_id": ("String", 36, None),
        "state": ("String", 32, None),
        "suppressed_by_candidate_id": ("String", 36, None),
        "manual_rearm_source_attempt_id": ("String", 36, None),
        "manual_rearm_authorized_at": ("DateTime", None, True),
    },
    "SeatObservation": {
        "id": ("String", 36, None),
        "candidate_id": ("String", 36, None),
        "source": ("String", 80, None),
        "observed_at": ("DateTime", None, True),
        "fresh_until": ("DateTime", None, True),
        "error_category": ("String", 80, None),
    },
    "ReservationAttempt": {
        "id": ("String", 36, None),
        "candidate_id": ("String", 36, None),
        "attempt_sequence": ("Integer", None, None),
        "episode_key": ("String", 128, None),
        "idempotency_key": ("String", 128, None),
        "started_at": ("DateTime", None, True),
        "finished_at": ("DateTime", None, True),
        "payment_deadline": ("DateTime", None, True),
        "official_handoff_url": ("Text", None, None),
        "credential_version": ("Integer", None, None),
        "confirmation_source": ("String", 80, None),
        "confirmation_observed_at": ("DateTime", None, True),
        "last_reconciled_at": ("DateTime", None, True),
        "reconciliation_attempt_count": ("Integer", None, None),
        "next_reconcile_at": ("DateTime", None, True),
        "post_deadline_reconciled_at": ("DateTime", None, True),
        "progress_stages": ("JSON", None, None),
        "reserved_seats": ("JSON", None, None),
        "confirmation_correlation_seats": ("JSON", None, None),
    },
    "WatchTransitionHistory": {
        "id": ("String", 36, None),
        "watch_id": ("String", 36, None),
        "reason": ("String", 160, None),
        "observation_id": ("String", 36, None),
        "created_at": ("DateTime", None, True),
    },
}
SCALAR_CLIENT_DEFAULTS = {
    "Watch": {
        "seat_class": "standard",
        "passenger_count": 1,
        "mode": "official",
        "reservation_policy": ReservationPolicy.NOTIFY_ONLY,
        "seat_observation_mode": SeatObservationMode.BALANCED,
        "focused_observation_interval_seconds": 25,
        "status": WatchStatus.DRAFT,
        "reservation_attempted": False,
        "unchanged_runs": 0,
    },
    "WatchCandidate": {
        "operational_status": OperationalStatus.UNKNOWN,
        "booking_window_status": BookingWindowStatus.UNKNOWN,
        "state": "active",
    },
    "SeatObservation": {},
    "ReservationAttempt": {
        "attempt_sequence": 1,
        "outcome": ReservationOutcome.PENDING,
        "reconciliation_attempt_count": 0,
    },
    "WatchTransitionHistory": {},
}
FOREIGN_KEYS = {
    "Watch": set(),
    "WatchCandidate": {
        ("watch_id", "watches.id", "CASCADE"),
        ("registration_evidence_id", "timetable_seat_evidence.id", "RESTRICT"),
        ("suppressed_by_candidate_id", "watch_candidates.id", "SET NULL"),
    },
    "SeatObservation": {("candidate_id", "watch_candidates.id", "CASCADE")},
    "ReservationAttempt": {("candidate_id", "watch_candidates.id", "CASCADE")},
    "WatchTransitionHistory": {
        ("watch_id", "watches.id", "CASCADE"),
        ("observation_id", "seat_observations.id", "SET NULL"),
    },
}
CLIENT_DEFAULT_COLUMNS = {
    "Watch": {
        "id",
        "seat_class",
        "passenger_count",
        "train_numbers",
        "notification_channel_ids",
        "mode",
        "reservation_policy",
        "seat_observation_mode",
        "focused_observation_interval_seconds",
        "status",
        "reservation_attempted",
        "unchanged_runs",
        "created_at",
        "updated_at",
    },
    "WatchCandidate": {
        "id",
        "scheduled_departure_at",
        "operational_status",
        "booking_window_status",
        "state",
    },
    "SeatObservation": {"id"},
    "ReservationAttempt": {
        "id",
        "attempt_sequence",
        "episode_key",
        "started_at",
        "outcome",
        "result_reason_code",
        "reconciliation_attempt_count",
        "progress_stages",
        "reserved_seats",
        "confirmation_correlation_seats",
    },
    "WatchTransitionHistory": {"id", "created_at"},
}
SERVER_DEFAULTS = {
    "Watch": {
        "reservation_policy": "NOTIFY_ONLY",
        "seat_observation_mode": "BALANCED",
        "focused_observation_interval_seconds": "25",
        "unchanged_runs": "0",
    },
    "WatchCandidate": {
        "operational_status": "UNKNOWN",
        "booking_window_status": "UNKNOWN",
        "state": "active",
    },
    "SeatObservation": {},
    "ReservationAttempt": {
        "attempt_sequence": "1",
        "episode_key": "legacy",
        "reconciliation_attempt_count": "0",
        "progress_stages": "[]",
        "reserved_seats": "[]",
        "confirmation_correlation_seats": "[]",
    },
    "WatchTransitionHistory": {},
}
ENUM_COLUMNS = {
    ("Watch", "provider"): Provider,
    ("Watch", "reservation_policy"): ReservationPolicy,
    ("Watch", "seat_observation_mode"): SeatObservationMode,
    ("Watch", "status"): WatchStatus,
    ("WatchCandidate", "operational_status"): OperationalStatus,
    ("WatchCandidate", "booking_window_status"): BookingWindowStatus,
    ("SeatObservation", "status"): SeatObservationStatus,
    ("ReservationAttempt", "outcome"): ReservationOutcome,
    ("ReservationAttempt", "result_reason_code"): ReservationResultReasonCode,
    ("ReservationAttempt", "confirmation_outcome"): ReservationConfirmationOutcome,
    (
        "ReservationAttempt",
        "confirmation_diagnostic_code",
    ): ReservationConfirmationDiagnosticCode,
    (
        "ReservationAttempt",
        "reconciliation_resolution",
    ): ReservationReconciliationResolution,
    ("WatchTransitionHistory", "from_status"): WatchStatus,
    ("WatchTransitionHistory", "to_status"): WatchStatus,
}
RELATIONSHIPS = {
    ("Watch", "candidates"): (
        "WatchCandidate",
        "watch",
        ("delete", "delete-orphan", "expunge", "merge", "refresh-expire", "save-update"),
        "selectin",
        False,
        ("watch_candidates.priority",),
        (),
        ("watch_id",),
    ),
    ("Watch", "transition_history"): (
        "WatchTransitionHistory",
        "watch",
        ("delete", "delete-orphan", "expunge", "merge", "refresh-expire", "save-update"),
        "select",
        True,
        ("watch_transition_history.created_at",),
        (),
        ("watch_id",),
    ),
    ("WatchCandidate", "watch"): (
        "Watch",
        "candidates",
        ("merge", "save-update"),
        "select",
        False,
        (),
        (),
        ("id",),
    ),
    ("WatchCandidate", "registration_evidence"): (
        "TimetableSeatEvidence",
        None,
        ("merge", "save-update"),
        "joined",
        False,
        (),
        (),
        ("id",),
    ),
    ("WatchCandidate", "suppressed_by_candidate"): (
        "WatchCandidate",
        "suppressed_candidates",
        ("merge", "save-update"),
        "select",
        False,
        (),
        ("suppressed_by_candidate_id",),
        ("id",),
    ),
    ("WatchCandidate", "suppressed_candidates"): (
        "WatchCandidate",
        "suppressed_by_candidate",
        ("merge", "save-update"),
        "select",
        True,
        (),
        ("suppressed_by_candidate_id",),
        ("suppressed_by_candidate_id",),
    ),
    ("WatchCandidate", "observations"): (
        "SeatObservation",
        "candidate",
        ("delete", "delete-orphan", "expunge", "merge", "refresh-expire", "save-update"),
        "select",
        True,
        ("seat_observations.observed_at",),
        (),
        ("candidate_id",),
    ),
    ("WatchCandidate", "reservation_attempts"): (
        "ReservationAttempt",
        "candidate",
        ("delete", "delete-orphan", "expunge", "merge", "refresh-expire", "save-update"),
        "select",
        True,
        ("reservation_attempts.attempt_sequence", "reservation_attempts.started_at"),
        (),
        ("candidate_id",),
    ),
    ("SeatObservation", "candidate"): (
        "WatchCandidate",
        "observations",
        ("merge", "save-update"),
        "select",
        False,
        (),
        (),
        ("id",),
    ),
    ("SeatObservation", "transition_history"): (
        "WatchTransitionHistory",
        "observation",
        ("merge", "save-update"),
        "select",
        True,
        (),
        (),
        ("observation_id",),
    ),
    ("ReservationAttempt", "candidate"): (
        "WatchCandidate",
        "reservation_attempts",
        ("merge", "save-update"),
        "select",
        False,
        (),
        (),
        ("id",),
    ),
    ("WatchTransitionHistory", "watch"): (
        "Watch",
        "transition_history",
        ("merge", "save-update"),
        "select",
        False,
        (),
        (),
        ("id",),
    ),
    ("WatchTransitionHistory", "observation"): (
        "SeatObservation",
        "transition_history",
        ("merge", "save-update"),
        "select",
        False,
        (),
        (),
        ("id",),
    ),
}


def test_watch_graph_legacy_aliases_have_one_canonical_mapper_and_table() -> None:
    configure_mappers()
    for name in MODEL_NAMES:
        legacy_class = getattr(legacy, name)
        canonical_class = getattr(canonical, name)
        assert legacy_class is canonical_class
        assert canonical_class.__module__ == "rail_waitlist.watch_management.models"
        assert canonical_class.__mapper__.local_table is canonical_class.__table__
        assert Base.metadata.tables[canonical_class.__tablename__] is canonical_class.__table__
        assert sum(mapper.class_ is canonical_class for mapper in Base.registry.mappers) == 1
    assert legacy.utcnow is canonical.utcnow


def test_watch_graph_column_order_nullability_and_keys_are_preserved() -> None:
    for name in MODEL_NAMES:
        table = getattr(canonical, name).__table__
        assert tuple(column.name for column in table.columns) == COLUMN_ORDERS[name]
        assert {column.name for column in table.columns if column.nullable} == NULLABLE_COLUMNS[
            name
        ]
        assert [column.name for column in table.primary_key.columns] == ["id"]


def test_watch_graph_indexes_constraints_and_foreign_keys_are_preserved() -> None:
    for name in MODEL_NAMES:
        table = getattr(canonical, name).__table__
        assert {
            (index.name, index.unique, tuple(column.name for column in index.columns))
            for index in table.indexes
        } == INDEXES[name]
        assert {
            (constraint.name, tuple(column.name for column in constraint.columns))
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        } == UNIQUE_CONSTRAINTS[name]
        assert {
            constraint.name: str(constraint.sqltext)
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        } == CHECK_CONSTRAINTS[name]
        assert {
            (column.name, foreign_key.target_fullname, foreign_key.ondelete)
            for column in table.columns
            for foreign_key in column.foreign_keys
        } == FOREIGN_KEYS[name]


def test_watch_graph_defaults_onupdate_and_enum_storage_are_preserved() -> None:
    for name in MODEL_NAMES:
        table = getattr(canonical, name).__table__
        assert {
            column.name: (
                type(column.type).__name__,
                getattr(column.type, "length", None),
                getattr(column.type, "timezone", None),
            )
            for column in table.columns
            if not isinstance(column.type, Enum)
        } == NON_ENUM_TYPE_SIGNATURES[name]
        assert {column.name for column in table.columns if column.default is not None} == (
            CLIENT_DEFAULT_COLUMNS[name]
        )
        assert {
            column.name: column.default.arg
            for column in table.columns
            if column.default is not None and column.default.is_scalar
        } == SCALAR_CLIENT_DEFAULTS[name]
        assert {
            column.name: str(column.server_default.arg)
            for column in table.columns
            if column.server_default is not None
        } == SERVER_DEFAULTS[name]
        expected_onupdate = {"updated_at"} if name == "Watch" else set()
        assert {column.name for column in table.columns if column.onupdate is not None} == (
            expected_onupdate
        )
    for (model_name, column_name), enum_class in ENUM_COLUMNS.items():
        column_type = getattr(canonical, model_name).__table__.c[column_name].type
        assert isinstance(column_type, Enum)
        assert column_type.enum_class is enum_class
        assert not column_type.native_enum
        assert column_type.enums == [member.name for member in enum_class]


def test_watch_graph_callable_defaults_keep_behavior() -> None:
    model_classes = [getattr(canonical, name) for name in MODEL_NAMES]
    for model_class in model_classes:
        default = model_class.__table__.c.id.default
        assert default is not None and default.is_callable
        uuid.UUID(default.arg(None))

    watch_table = canonical.Watch.__table__
    first_train_numbers = watch_table.c.train_numbers.default.arg(None)
    second_train_numbers = watch_table.c.train_numbers.default.arg(None)
    assert first_train_numbers == second_train_numbers == []
    assert first_train_numbers is not second_train_numbers
    first_channels = watch_table.c.notification_channel_ids.default.arg(None)
    second_channels = watch_table.c.notification_channel_ids.default.arg(None)
    assert first_channels == second_channels == []
    assert first_channels is not second_channels

    attempt_table = canonical.ReservationAttempt.__table__
    first_progress = attempt_table.c.progress_stages.default.arg(None)
    second_progress = attempt_table.c.progress_stages.default.arg(None)
    assert first_progress == second_progress == []
    assert first_progress is not second_progress
    first_seats = attempt_table.c.reserved_seats.default.arg(None)
    second_seats = attempt_table.c.reserved_seats.default.arg(None)
    assert first_seats == second_seats == []
    assert first_seats is not second_seats
    first_correlation_seats = attempt_table.c.confirmation_correlation_seats.default.arg(None)
    second_correlation_seats = attempt_table.c.confirmation_correlation_seats.default.arg(None)
    assert first_correlation_seats == second_correlation_seats == []
    assert first_correlation_seats is not second_correlation_seats

    departure = datetime(2026, 8, 7, 12, tzinfo=UTC)

    class DefaultContext:
        @staticmethod
        def get_current_parameters() -> dict[str, datetime]:
            return {"departure_at": departure}

    scheduled_default = canonical.WatchCandidate.__table__.c.scheduled_departure_at.default
    assert scheduled_default is not None and scheduled_default.is_callable
    assert scheduled_default.arg(DefaultContext()) is departure

    episode_default = canonical.ReservationAttempt.__table__.c.episode_key.default
    assert episode_default is not None and episode_default.is_callable
    episode_key = episode_default.arg(None)
    assert episode_key.startswith("legacy:")
    uuid.UUID(episode_key.removeprefix("legacy:"))

    class AttemptDefaultContext:
        @staticmethod
        def get_current_parameters() -> dict[str, ReservationOutcome]:
            return {"outcome": ReservationOutcome.UNKNOWN}

    result_reason_default = attempt_table.c.result_reason_code.default
    assert result_reason_default is not None and result_reason_default.is_callable
    assert (
        result_reason_default.arg(AttemptDefaultContext())
        is ReservationResultReasonCode.RESERVATION_REQUEST_RESULT_UNKNOWN
    )

    for column in (
        canonical.Watch.__table__.c.created_at,
        canonical.Watch.__table__.c.updated_at,
        canonical.ReservationAttempt.__table__.c.started_at,
        canonical.WatchTransitionHistory.__table__.c.created_at,
    ):
        assert isinstance(column.type, DateTime) and column.type.timezone
        value = column.default.arg(None)
        assert isinstance(value, datetime) and value.tzinfo is UTC
    updated_at = canonical.Watch.__table__.c.updated_at
    update_value = updated_at.onupdate.arg(None)
    assert isinstance(update_value, datetime) and update_value.tzinfo is UTC


def test_watch_graph_relationship_contract_is_preserved() -> None:
    configure_mappers()
    actual: dict[tuple[str, str], tuple[object, ...]] = {}
    for model_name in MODEL_NAMES:
        model_class = getattr(canonical, model_name)
        for relation in model_class.__mapper__.relationships:
            order_by = (
                () if relation.order_by is False else tuple(str(item) for item in relation.order_by)
            )
            actual[(model_name, relation.key)] = (
                relation.mapper.class_.__name__,
                relation.back_populates,
                tuple(sorted(relation.cascade)),
                relation.lazy,
                relation.passive_deletes,
                order_by,
                tuple(sorted(column.name for column in relation._user_defined_foreign_keys)),
                tuple(sorted(column.name for column in relation.remote_side)),
            )

    assert actual == RELATIONSHIPS
    registration_relation = canonical.WatchCandidate.__mapper__.relationships[
        "registration_evidence"
    ]
    assert registration_relation.mapper.class_ is TimetableSeatEvidence


def test_candidate_legacy_reservation_attempt_property_is_preserved() -> None:
    candidate = canonical.WatchCandidate()
    first = canonical.ReservationAttempt(attempt_sequence=1)
    second = canonical.ReservationAttempt(attempt_sequence=2)
    assert candidate.reservation_attempt is None
    candidate.reservation_attempt = first
    assert candidate.reservation_attempt is first
    assert list(candidate.reservation_attempts) == [first]
    candidate.reservation_attempt = second
    assert candidate.reservation_attempt is second
    assert list(candidate.reservation_attempts) == [second]
    candidate.reservation_attempt = None
    assert candidate.reservation_attempt is None
    assert list(candidate.reservation_attempts) == []


@pytest.mark.parametrize(
    "import_order",
    [
        "canonical-first",
        "legacy-first",
        "schemas-first",
        "confirmation-first",
        "timetable-first",
    ],
)
def test_watch_graph_import_orders_configure_one_mapper_graph(import_order: str) -> None:
    script = r"""
import json
import sys
from sqlalchemy.orm import configure_mappers

if sys.argv[1] == "canonical-first":
    from rail_waitlist.watch_management import models as canonical
elif sys.argv[1] == "legacy-first":
    from rail_waitlist import models as legacy
elif sys.argv[1] == "schemas-first":
    from rail_waitlist import schemas
elif sys.argv[1] == "confirmation-first":
    from rail_waitlist import reservation_confirmation
else:
    from rail_waitlist.timetable_management import models as timetable_models

from rail_waitlist import models as legacy
from rail_waitlist.database import Base
from rail_waitlist.watch_management import models as canonical

configure_mappers()
names = (
    "Watch",
    "WatchCandidate",
    "SeatObservation",
    "ReservationAttempt",
    "WatchTransitionHistory",
)
print(json.dumps({
    "identity": all(getattr(legacy, name) is getattr(canonical, name) for name in names),
    "metadata": all(
        Base.metadata.tables[getattr(canonical, name).__tablename__]
        is getattr(canonical, name).__table__
        for name in names
    ),
    "modules": sorted({getattr(canonical, name).__module__ for name in names}),
    "mapper_counts": {
        name: sum(mapper.class_ is getattr(canonical, name) for mapper in Base.registry.mappers)
        for name in names
    },
    "relationship_targets": {
        name: sorted(
            relation.mapper.class_.__name__
            for relation in getattr(canonical, name).__mapper__.relationships
        )
        for name in names
    },
}, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-W", "error", "-c", script, import_order],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result["identity"] is True
    assert result["metadata"] is True
    assert result["modules"] == ["rail_waitlist.watch_management.models"]
    assert result["mapper_counts"] == {name: 1 for name in MODEL_NAMES}
    assert result["relationship_targets"] == {
        "ReservationAttempt": ["WatchCandidate"],
        "SeatObservation": ["WatchCandidate", "WatchTransitionHistory"],
        "Watch": ["WatchCandidate", "WatchTransitionHistory"],
        "WatchCandidate": [
            "ReservationAttempt",
            "SeatObservation",
            "TimetableSeatEvidence",
            "Watch",
            "WatchCandidate",
            "WatchCandidate",
        ],
        "WatchTransitionHistory": ["SeatObservation", "Watch"],
    }
