"""Bound read-only reservation reconciliation attempts."""

import sqlalchemy as sa
from alembic import op

revision = "0021_bounded_reconcile"
down_revision = "0020_reservation_reconciliation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("reservation_attempts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "reconciliation_attempt_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column("next_reconcile_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_check_constraint(
            "ck_reservation_attempt_reconciliation_attempt_count_bounded",
            "reconciliation_attempt_count >= 0 AND reconciliation_attempt_count <= 3",
        )
        batch_op.create_index(
            "ix_reservation_attempts_next_reconcile_at",
            ["next_reconcile_at"],
            unique=False,
        )

    # A previous inconclusive one-shot is eligible for at most two additional
    # read-only confirmations. This does not re-arm or replay reservation_once.
    op.execute(
        sa.text(
            "UPDATE reservation_attempts "
            "SET reconciliation_attempt_count = 1, next_reconcile_at = CURRENT_TIMESTAMP "
            "WHERE last_reconciled_at IS NOT NULL "
            "AND confirmation_outcome IN ('NOT_FOUND', 'INCONCLUSIVE') "
            "AND outcome IN ('PAYMENT_REQUIRED', 'UNKNOWN') "
            "AND credential_version IS NOT NULL"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("reservation_attempts") as batch_op:
        batch_op.drop_index("ix_reservation_attempts_next_reconcile_at")
        batch_op.drop_constraint(
            "ck_reservation_attempt_reconciliation_attempt_count_bounded",
            type_="check",
        )
        batch_op.drop_column("next_reconcile_at")
        batch_op.drop_column("reconciliation_attempt_count")
