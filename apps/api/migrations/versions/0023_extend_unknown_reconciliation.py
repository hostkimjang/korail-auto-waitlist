"""Extend bounded UNKNOWN reservation reconciliation."""

from alembic import op

revision = "0023_extend_unknown_reconcile"
down_revision = "0022_post_deadline_check"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("reservation_attempts") as batch_op:
        batch_op.drop_constraint(
            "ck_reservation_attempt_reconciliation_attempt_count_bounded",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_reservation_attempt_reconciliation_attempt_count_bounded",
            "reconciliation_attempt_count >= 0 AND reconciliation_attempt_count <= 6",
        )


def downgrade() -> None:
    op.execute(
        "UPDATE reservation_attempts SET reconciliation_attempt_count = 3 "
        "WHERE reconciliation_attempt_count > 3"
    )
    with op.batch_alter_table("reservation_attempts") as batch_op:
        batch_op.drop_constraint(
            "ck_reservation_attempt_reconciliation_attempt_count_bounded",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_reservation_attempt_reconciliation_attempt_count_bounded",
            "reconciliation_attempt_count >= 0 AND reconciliation_attempt_count <= 3",
        )
