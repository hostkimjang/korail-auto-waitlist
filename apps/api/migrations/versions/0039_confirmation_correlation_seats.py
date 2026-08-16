"""Persist private exact-seat correlation for ambiguous reservation results."""

import sqlalchemy as sa
from alembic import op

revision = "0039_confirmation_corr_seats"
down_revision = "0038_reconciliation_resolution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reservation_attempts",
        sa.Column(
            "confirmation_correlation_seats",
            sa.JSON(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("reservation_attempts", "confirmation_correlation_seats")
