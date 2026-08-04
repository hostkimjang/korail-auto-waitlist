"""Add a one-shot post-deadline reservation confirmation marker."""

import sqlalchemy as sa
from alembic import op

revision = "0022_post_deadline_check"
down_revision = "0021_bounded_reconcile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("reservation_attempts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "post_deadline_reconciled_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch_op.create_index(
            "ix_reservation_attempts_post_deadline_reconciled_at",
            ["post_deadline_reconciled_at"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("reservation_attempts") as batch_op:
        batch_op.drop_index("ix_reservation_attempts_post_deadline_reconciled_at")
        batch_op.drop_column("post_deadline_reconciled_at")
