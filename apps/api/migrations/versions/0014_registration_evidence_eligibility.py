"""Fail closed when timetable evidence did not permit adding a watch."""

import sqlalchemy as sa
from alembic import op

# Alembic's default PostgreSQL version_num column is VARCHAR(32).
revision = "0014_evidence_eligibility"
down_revision = "0013_browser_standing_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("timetable_seat_evidence") as batch_op:
        batch_op.add_column(
            sa.Column(
                "registration_allowed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("timetable_seat_evidence") as batch_op:
        batch_op.drop_column("registration_allowed")
