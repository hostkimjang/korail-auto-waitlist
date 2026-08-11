"""Persist one explicit manual reservation rearm per ended payment hold."""

import sqlalchemy as sa
from alembic import op

revision = "0032_manual_reservation_rearm"
down_revision = "0031_watch_display_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("watch_candidates") as batch_op:
        batch_op.add_column(
            sa.Column("manual_rearm_source_attempt_id", sa.String(36), nullable=True)
        )
        batch_op.add_column(
            sa.Column("manual_rearm_authorized_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_check_constraint(
            "ck_watch_candidate_manual_rearm_shape",
            "(manual_rearm_source_attempt_id IS NULL "
            "AND manual_rearm_authorized_at IS NULL) "
            "OR (manual_rearm_source_attempt_id IS NOT NULL "
            "AND manual_rearm_authorized_at IS NOT NULL)",
        )
        batch_op.create_index(
            "ix_watch_candidates_manual_rearm_source_attempt_id",
            ["manual_rearm_source_attempt_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("watch_candidates") as batch_op:
        batch_op.drop_index("ix_watch_candidates_manual_rearm_source_attempt_id")
        batch_op.drop_constraint(
            "ck_watch_candidate_manual_rearm_shape",
            type_="check",
        )
        batch_op.drop_column("manual_rearm_authorized_at")
        batch_op.drop_column("manual_rearm_source_attempt_id")
