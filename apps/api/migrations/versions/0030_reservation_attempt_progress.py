"""Persist confirmed reservation progress for canonical reconnect recovery."""

import sqlalchemy as sa
from alembic import op

revision = "0030_attempt_progress"
down_revision = "0029_ui_refresh_interval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reservation_attempts",
        sa.Column(
            "progress_stages",
            sa.JSON(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("reservation_attempts", "progress_stages")
