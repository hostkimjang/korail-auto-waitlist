"""Persist verified train subtype and assigned reservation seats."""

import sqlalchemy as sa
from alembic import op

revision = "0031_watch_display_metadata"
down_revision = "0030_attempt_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "timetable_seat_evidence",
        sa.Column("train_type", sa.String(40), nullable=True),
    )
    op.add_column(
        "reservation_attempts",
        sa.Column(
            "reserved_seats",
            sa.JSON(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("reservation_attempts", "reserved_seats")
    op.drop_column("timetable_seat_evidence", "train_type")
