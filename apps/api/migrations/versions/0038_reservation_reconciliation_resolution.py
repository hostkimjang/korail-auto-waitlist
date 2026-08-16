"""Persist terminal resolution for bounded UNKNOWN reconciliation."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0038_reconciliation_resolution"
down_revision = "0037_standing_only_status"
branch_labels = None
depends_on = None

_RESOLUTIONS = ("CONFIRMED_ABSENT", "EXHAUSTED_UNRESOLVED")
_ALLOWED_CONSTRAINT = "ck_reservation_attempt_reconcile_resolution_allowed"
_SHAPE_CONSTRAINT = "ck_reservation_attempt_reconcile_resolution_shape"


def upgrade() -> None:
    resolution_type = sa.Enum(
        *_RESOLUTIONS,
        name="reservationreconciliationresolution",
        native_enum=False,
    )
    with op.batch_alter_table("reservation_attempts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "reconciliation_resolution",
                resolution_type,
                nullable=True,
            )
        )

    # Through 0037, AUTH/BLOCK confirmations consumed one evidence attempt even
    # though no official reservation state was observed. Remove exactly that one
    # historical increment; zero-count rows already match the corrected runtime.
    op.execute(
        sa.text(
            "UPDATE reservation_attempts "
            "SET reconciliation_attempt_count = reconciliation_attempt_count - 1 "
            "WHERE confirmation_outcome IN ('AUTH_REQUIRED', 'PROVIDER_BLOCKED') "
            "AND reconciliation_attempt_count > 0"
        )
    )

    # Preserve the pre-migration distinction before normalizing the stranded
    # count-six NOT_FOUND schedule to a terminal unresolved state.
    op.execute(
        sa.text(
            "UPDATE reservation_attempts "
            "SET reconciliation_resolution = 'CONFIRMED_ABSENT' "
            "WHERE outcome = 'UNKNOWN' AND confirmation_outcome = 'NOT_FOUND' "
            "AND next_reconcile_at IS NULL AND reconciliation_attempt_count >= 1 "
            "AND last_reconciled_at IS NOT NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE reservation_attempts "
            "SET reconciliation_resolution = 'EXHAUSTED_UNRESOLVED', "
            "last_reconciled_at = COALESCE(last_reconciled_at, confirmation_observed_at), "
            "next_reconcile_at = NULL "
            "WHERE outcome = 'UNKNOWN' AND reconciliation_attempt_count >= 6 "
            "AND confirmation_outcome = 'INCONCLUSIVE'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE reservation_attempts "
            "SET reconciliation_resolution = 'EXHAUSTED_UNRESOLVED', "
            "last_reconciled_at = COALESCE(last_reconciled_at, confirmation_observed_at), "
            "next_reconcile_at = NULL "
            "WHERE outcome = 'UNKNOWN' AND reconciliation_attempt_count >= 6 "
            "AND next_reconcile_at IS NOT NULL AND confirmation_outcome = 'NOT_FOUND'"
        )
    )

    allowed = ", ".join(f"'{name}'" for name in _RESOLUTIONS)
    with op.batch_alter_table("reservation_attempts") as batch_op:
        batch_op.create_check_constraint(
            _ALLOWED_CONSTRAINT,
            f"reconciliation_resolution IS NULL OR reconciliation_resolution IN ({allowed})",
        )
        batch_op.create_check_constraint(
            _SHAPE_CONSTRAINT,
            "reconciliation_resolution IS NULL OR "
            "(reconciliation_resolution = 'CONFIRMED_ABSENT' "
            "AND outcome = 'UNKNOWN' AND confirmation_outcome = 'NOT_FOUND' "
            "AND confirmation_observed_at IS NOT NULL AND last_reconciled_at IS NOT NULL "
            "AND reconciliation_attempt_count >= 1 AND next_reconcile_at IS NULL) OR "
            "(reconciliation_resolution = 'EXHAUSTED_UNRESOLVED' "
            "AND outcome = 'UNKNOWN' "
            "AND confirmation_outcome IN ('INCONCLUSIVE', 'NOT_FOUND') "
            "AND confirmation_observed_at IS NOT NULL AND last_reconciled_at IS NOT NULL "
            "AND reconciliation_attempt_count >= 6 AND next_reconcile_at IS NULL)",
        )


def downgrade() -> None:
    # Old code inferred confirmed absence from NOT_FOUND + a null schedule. Give
    # unresolved single-NOT_FOUND rows a non-null inert marker before dropping the
    # explicit resolution so a rollback cannot authorize a duplicate reservation.
    with op.batch_alter_table("reservation_attempts") as batch_op:
        batch_op.drop_constraint(_SHAPE_CONSTRAINT, type_="check")

    op.execute(
        sa.text(
            "UPDATE reservation_attempts SET next_reconcile_at = "
            "COALESCE(last_reconciled_at, confirmation_observed_at, finished_at, started_at) "
            "WHERE reconciliation_resolution = 'EXHAUSTED_UNRESOLVED' "
            "AND confirmation_outcome = 'NOT_FOUND'"
        )
    )
    # A 0037 runtime counted the latest AUTH/BLOCK read. Restore that bounded
    # representation for both migrated and newly written corrected rows.
    op.execute(
        sa.text(
            "UPDATE reservation_attempts "
            "SET reconciliation_attempt_count = CASE "
            "WHEN reconciliation_attempt_count < 6 "
            "THEN reconciliation_attempt_count + 1 "
            "ELSE reconciliation_attempt_count END "
            "WHERE confirmation_outcome IN ('AUTH_REQUIRED', 'PROVIDER_BLOCKED')"
        )
    )
    with op.batch_alter_table("reservation_attempts") as batch_op:
        batch_op.drop_constraint(_ALLOWED_CONSTRAINT, type_="check")
        batch_op.drop_column("reconciliation_resolution")
