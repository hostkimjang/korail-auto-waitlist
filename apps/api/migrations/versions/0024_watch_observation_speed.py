"""Add per-watch cancellation-seat observation speed controls."""

import sqlalchemy as sa
from alembic import op

revision = "0024_watch_observation_speed"
down_revision = "0023_extend_unknown_reconcile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    observation_mode = sa.Enum(
        "BALANCED",
        "FOCUSED",
        name="seatobservationmode",
        native_enum=False,
    )
    with op.batch_alter_table("watches") as batch_op:
        batch_op.add_column(
            sa.Column(
                "seat_observation_mode",
                observation_mode,
                nullable=False,
                server_default="BALANCED",
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
            "ck_watch_seat_observation_mode_allowed",
            "seat_observation_mode IN ('BALANCED', 'FOCUSED')",
        )
        batch_op.create_check_constraint(
            "ck_watch_focused_observation_interval_seconds",
            "focused_observation_interval_seconds BETWEEN 20 AND 30",
        )


def downgrade() -> None:
    with op.batch_alter_table("watches") as batch_op:
        batch_op.drop_constraint(
            "ck_watch_focused_observation_interval_seconds", type_="check"
        )
        batch_op.drop_constraint("ck_watch_seat_observation_mode_allowed", type_="check")
        batch_op.drop_column("focused_observation_interval_seconds")
        batch_op.drop_column("seat_observation_mode")
