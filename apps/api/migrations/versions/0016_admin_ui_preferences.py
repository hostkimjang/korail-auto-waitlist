"""Persist administrator UI preferences."""

import sqlalchemy as sa
from alembic import op

revision = "0016_admin_ui_preferences"
down_revision = "0015_execution_lease"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("admin_accounts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "timetable_refresh_interval_seconds",
                sa.Integer(),
                nullable=False,
                server_default="5",
            )
        )
        batch_op.add_column(
            sa.Column(
                "preferences_updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )
        batch_op.create_check_constraint(
            "ck_admin_account_timetable_refresh_interval_seconds",
            "timetable_refresh_interval_seconds BETWEEN 5 AND 300",
        )


def downgrade() -> None:
    with op.batch_alter_table("admin_accounts") as batch_op:
        batch_op.drop_constraint(
            "ck_admin_account_timetable_refresh_interval_seconds",
            type_="check",
        )
        batch_op.drop_column("preferences_updated_at")
        batch_op.drop_column("timetable_refresh_interval_seconds")
