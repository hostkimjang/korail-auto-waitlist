"""Allow one-second UI refresh intervals."""

from alembic import op

revision = "0029_ui_refresh_interval"
down_revision = "0028_web_push_device_key"
branch_labels = None
depends_on = None

CONSTRAINT_NAME = "ck_admin_account_timetable_refresh_interval_seconds"


def upgrade() -> None:
    with op.batch_alter_table("admin_accounts") as batch_op:
        batch_op.drop_constraint(CONSTRAINT_NAME, type_="check")
        batch_op.create_check_constraint(
            CONSTRAINT_NAME,
            "timetable_refresh_interval_seconds BETWEEN 1 AND 300",
        )


def downgrade() -> None:
    op.execute(
        "UPDATE admin_accounts "
        "SET timetable_refresh_interval_seconds = 5 "
        "WHERE timetable_refresh_interval_seconds < 5"
    )
    with op.batch_alter_table("admin_accounts") as batch_op:
        batch_op.drop_constraint(CONSTRAINT_NAME, type_="check")
        batch_op.create_check_constraint(
            CONSTRAINT_NAME,
            "timetable_refresh_interval_seconds BETWEEN 5 AND 300",
        )
