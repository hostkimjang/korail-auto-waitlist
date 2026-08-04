"""Add observation, attempt, transition, and provider circuit persistence."""

import sqlalchemy as sa
from alembic import op

revision = "0005_persistence_foundation"
down_revision = "0004_watch_candidates"
branch_labels = None
depends_on = None


seat_observation_status = sa.Enum(
    "UNAVAILABLE",
    "UNKNOWN",
    "AVAILABLE",
    "LIMITED",
    "STANDING_PLUS_SEAT",
    "NOT_ENOUGH_SEATS",
    "SOLD_OUT",
    "WAITLIST_AVAILABLE",
    "RESERVATION_COMPLETED",
    "NOT_OFFERED",
    "DEPARTED",
    "OUT_OF_SERVICE",
    "STALE",
    "ERROR",
    name="seatobservationstatus",
    native_enum=False,
)
reservation_outcome = sa.Enum(
    "PENDING",
    "PAYMENT_REQUIRED",
    "RESERVED",
    "NOT_AVAILABLE",
    "AUTH_REQUIRED",
    "PROVIDER_BLOCKED",
    "FAILED",
    "UNKNOWN",
    name="reservationoutcome",
    native_enum=False,
)
provider = sa.Enum("KORAIL", "SRT", "MOCK", name="provider", native_enum=False)
provider_circuit_state = sa.Enum(
    "CLOSED",
    "OPEN",
    "HALF_OPEN",
    "MANUAL_HOLD",
    name="providercircuitstate",
    native_enum=False,
)
watch_status = sa.Enum(
    "DRAFT",
    "SCHEDULED",
    "WATCHING",
    "OFFICIAL_WAITLIST",
    "SEAT_FOUND",
    "RESERVING",
    "PAYMENT_REQUIRED",
    "COMPLETED",
    "PAUSED",
    "COOLDOWN",
    "AUTH_REQUIRED",
    "EXPIRED",
    "FAILED",
    name="watchstatus",
    native_enum=False,
)


def upgrade() -> None:
    with op.batch_alter_table("watches") as batch_op:
        batch_op.add_column(
            sa.Column(
                "unchanged_runs",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.create_check_constraint(
            "ck_watch_unchanged_runs_nonnegative", "unchanged_runs >= 0"
        )

    with op.batch_alter_table("watch_candidates") as batch_op:
        batch_op.add_column(
            sa.Column(
                "state",
                sa.String(32),
                nullable=False,
                server_default=sa.text("'active'"),
            )
        )
        batch_op.add_column(
            sa.Column("suppressed_by_candidate_id", sa.String(36), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_watch_candidates_suppressed_by_candidate_id",
            "watch_candidates",
            ["suppressed_by_candidate_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_check_constraint(
            "ck_watch_candidate_state_allowed",
            "state IN ('active', 'observed', 'seat_found', 'reservation_attempted', "
            "'payment_required', 'suppressed_by_priority', 'expired', 'failed')",
        )
        batch_op.create_check_constraint(
            "ck_watch_candidate_not_self_suppressed",
            "suppressed_by_candidate_id IS NULL OR suppressed_by_candidate_id <> id",
        )
        batch_op.create_index(
            "ix_watch_candidates_suppressed_by_candidate_id",
            ["suppressed_by_candidate_id"],
        )
        batch_op.create_index(
            "ix_watch_candidates_watch_state", ["watch_id", "state"]
        )

    op.create_table(
        "seat_observations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "candidate_id",
            sa.String(36),
            sa.ForeignKey("watch_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", seat_observation_status, nullable=False),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fresh_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_category", sa.String(80)),
        sa.CheckConstraint(
            "length(trim(source)) > 0", name="ck_seat_observation_source_nonempty"
        ),
        sa.CheckConstraint(
            "fresh_until >= observed_at", name="ck_seat_observation_freshness_order"
        ),
        sa.CheckConstraint(
            "error_category IS NULL OR length(trim(error_category)) > 0",
            name="ck_seat_observation_error_category_nonempty",
        ),
        sa.CheckConstraint(
            "status IN ('UNAVAILABLE', 'UNKNOWN', 'AVAILABLE', 'LIMITED', "
            "'STANDING_PLUS_SEAT', 'NOT_ENOUGH_SEATS', 'SOLD_OUT', "
            "'WAITLIST_AVAILABLE', 'RESERVATION_COMPLETED', 'NOT_OFFERED', "
            "'DEPARTED', 'OUT_OF_SERVICE', 'STALE', 'ERROR')",
            name="ck_seat_observation_status_allowed",
        ),
    )
    op.create_index(
        "ix_seat_observations_candidate_observed_at",
        "seat_observations",
        ["candidate_id", "observed_at"],
    )
    op.create_index(
        "ix_seat_observations_status_fresh_until",
        "seat_observations",
        ["status", "fresh_until"],
    )

    op.create_table(
        "reservation_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "candidate_id",
            sa.String(36),
            sa.ForeignKey("watch_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", reservation_outcome, nullable=False),
        sa.Column("payment_deadline", sa.DateTime(timezone=True)),
        sa.Column("official_handoff_url", sa.Text()),
        sa.CheckConstraint(
            "length(trim(idempotency_key)) > 0",
            name="ck_reservation_attempt_idempotency_key_nonempty",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_reservation_attempt_timestamp_order",
        ),
        sa.CheckConstraint(
            "official_handoff_url IS NULL OR official_handoff_url LIKE 'https://%'",
            name="ck_reservation_attempt_handoff_https",
        ),
        sa.CheckConstraint(
            "outcome IN ('PENDING', 'PAYMENT_REQUIRED', 'RESERVED', 'NOT_AVAILABLE', "
            "'AUTH_REQUIRED', 'PROVIDER_BLOCKED', 'FAILED', 'UNKNOWN')",
            name="ck_reservation_attempt_outcome_allowed",
        ),
        sa.UniqueConstraint(
            "candidate_id", name="uq_reservation_attempt_candidate_id"
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_reservation_attempt_idempotency_key"
        ),
    )
    op.create_index(
        "ix_reservation_attempts_outcome_started_at",
        "reservation_attempts",
        ["outcome", "started_at"],
    )

    # 0004 only had a watch-level boolean, so the attempted candidate is unknowable.
    # Backfill every persisted candidate as UNKNOWN to preserve the one-attempt boundary
    # rather than risking another provider call after upgrade.
    op.execute(
        """
        INSERT INTO reservation_attempts (
            id, candidate_id, idempotency_key, started_at, finished_at,
            outcome, payment_deadline, official_handoff_url
        )
        SELECT
            c.id,
            c.id,
            'legacy-reservation:' || c.id,
            w.updated_at,
            w.updated_at,
            'UNKNOWN',
            NULL,
            NULL
        FROM watch_candidates AS c
        JOIN watches AS w ON w.id = c.watch_id
        WHERE w.reservation_attempted = true
        """
    )
    op.execute(
        """
        UPDATE watch_candidates
        SET state = 'failed'
        WHERE watch_id IN (
            SELECT id FROM watches WHERE reservation_attempted = true
        )
        """
    )

    op.create_table(
        "watch_transition_history",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "watch_id",
            sa.String(36),
            sa.ForeignKey("watches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_status", watch_status, nullable=False),
        sa.Column("to_status", watch_status, nullable=False),
        sa.Column("reason", sa.String(160), nullable=False),
        sa.Column(
            "observation_id",
            sa.String(36),
            sa.ForeignKey("seat_observations.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "from_status <> to_status", name="ck_watch_transition_status_changed"
        ),
        sa.CheckConstraint(
            "length(trim(reason)) > 0", name="ck_watch_transition_reason_nonempty"
        ),
    )
    op.create_index(
        "ix_watch_transition_history_observation_id",
        "watch_transition_history",
        ["observation_id"],
    )
    op.create_index(
        "ix_watch_transition_history_watch_created_at",
        "watch_transition_history",
        ["watch_id", "created_at"],
    )

    legacy_active_predicate = """
        reservation_attempted = true
        AND status IN (
            'SCHEDULED', 'WATCHING', 'OFFICIAL_WAITLIST',
            'SEAT_FOUND', 'RESERVING'
        )
    """
    op.execute(
        f"""
        INSERT INTO watch_transition_history (
            id, watch_id, from_status, to_status, reason,
            observation_id, created_at
        )
        SELECT
            w.id,
            w.id,
            w.status,
            'AUTH_REQUIRED',
            'legacy_reservation_attempt_requires_manual_check',
            NULL,
            CURRENT_TIMESTAMP
        FROM watches AS w
        WHERE {legacy_active_predicate}
        """
    )
    op.execute(
        f"""
        INSERT INTO outbox_events (
            id, aggregate_type, aggregate_id, event_type, payload,
            dedupe_key, status, attempts, available_at, processed_at,
            last_error, created_at
        )
        SELECT
            'legacy-' || substr(w.id, 1, 29),
            'watch',
            w.id,
            'watch.reservation_attempt_recovery_required',
            '{{}}',
            'legacy-reservation-recovery:' || w.id,
            'PENDING',
            0,
            CURRENT_TIMESTAMP,
            NULL,
            NULL,
            CURRENT_TIMESTAMP
        FROM watches AS w
        WHERE {legacy_active_predicate}
        """
    )
    op.execute(
        f"""
        UPDATE watches
        SET status = 'AUTH_REQUIRED', next_check_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE {legacy_active_predicate}
        """
    )

    op.create_table(
        "provider_circuits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider", provider, nullable=False),
        sa.Column("state", provider_circuit_state, nullable=False),
        sa.Column("reason", sa.String(160)),
        sa.Column("opened_at", sa.DateTime(timezone=True)),
        sa.Column("cooldown_until", sa.DateTime(timezone=True)),
        sa.Column("manual_resume_required", sa.Boolean(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "reason IS NULL OR length(trim(reason)) > 0",
            name="ck_provider_circuit_reason_nonempty",
        ),
        sa.CheckConstraint(
            "cooldown_until IS NULL OR opened_at IS NULL OR cooldown_until >= opened_at",
            name="ck_provider_circuit_cooldown_order",
        ),
        sa.CheckConstraint(
            "generation >= 0", name="ck_provider_circuit_generation_nonnegative"
        ),
        sa.CheckConstraint(
            "provider IN ('KORAIL', 'SRT', 'MOCK')",
            name="ck_provider_circuit_provider_allowed",
        ),
        sa.CheckConstraint(
            "state IN ('CLOSED', 'OPEN', 'HALF_OPEN', 'MANUAL_HOLD')",
            name="ck_provider_circuit_state_allowed",
        ),
        sa.UniqueConstraint("provider", name="uq_provider_circuit_provider"),
    )
    op.create_index(
        "ix_provider_circuits_state_cooldown",
        "provider_circuits",
        ["state", "cooldown_until"],
    )


def downgrade() -> None:
    op.drop_table("provider_circuits")
    op.drop_table("watch_transition_history")
    op.drop_table("reservation_attempts")
    op.drop_table("seat_observations")

    with op.batch_alter_table("watch_candidates") as batch_op:
        batch_op.drop_index("ix_watch_candidates_watch_state")
        batch_op.drop_index("ix_watch_candidates_suppressed_by_candidate_id")
        batch_op.drop_constraint(
            "ck_watch_candidate_not_self_suppressed", type_="check"
        )
        batch_op.drop_constraint("ck_watch_candidate_state_allowed", type_="check")
        batch_op.drop_constraint(
            "fk_watch_candidates_suppressed_by_candidate_id", type_="foreignkey"
        )
        batch_op.drop_column("suppressed_by_candidate_id")
        batch_op.drop_column("state")

    with op.batch_alter_table("watches") as batch_op:
        batch_op.drop_constraint(
            "ck_watch_unchanged_runs_nonnegative", type_="check"
        )
        batch_op.drop_column("unchanged_runs")
