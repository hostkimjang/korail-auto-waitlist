"""Replace split observation preferences with one global cadence."""

import sqlalchemy as sa
from alembic import op

revision = "0026_unified_observation"
down_revision = "0025_admin_observation_intervals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("admin_accounts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "observation_interval_seconds",
                sa.Integer(),
                nullable=False,
                server_default="5",
            )
        )
        batch_op.create_check_constraint(
            "ck_admin_account_observation_interval_seconds",
            "observation_interval_seconds BETWEEN 1 AND 600",
        )
        batch_op.drop_constraint(
            "ck_admin_account_focused_observation_interval_seconds", type_="check"
        )
        batch_op.drop_constraint(
            "ck_admin_account_balanced_observation_interval_seconds", type_="check"
        )
        batch_op.drop_column("focused_observation_interval_seconds")
        batch_op.drop_column("balanced_observation_interval_seconds")


def downgrade() -> None:
    with op.batch_alter_table("admin_accounts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "balanced_observation_interval_seconds",
                sa.Integer(),
                nullable=False,
                server_default="600",
            )
        )
        batch_op.add_column(
            sa.Column(
                "focused_observation_interval_seconds",
                sa.Integer(),
                nullable=False,
                server_default="25",
            )
        )
        batch_op.create_check_constraint(
            "ck_admin_account_balanced_observation_interval_seconds",
            "balanced_observation_interval_seconds BETWEEN 30 AND 600",
        )
        batch_op.create_check_constraint(
            "ck_admin_account_focused_observation_interval_seconds",
            "focused_observation_interval_seconds BETWEEN 20 AND 30",
        )
        batch_op.drop_constraint(
            "ck_admin_account_observation_interval_seconds", type_="check"
        )
        batch_op.drop_column("observation_interval_seconds")
