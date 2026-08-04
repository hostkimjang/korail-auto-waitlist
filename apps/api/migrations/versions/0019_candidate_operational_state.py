"""Separate immutable scheduled candidate identity from live operational state."""

import sqlalchemy as sa
from alembic import op

revision = "0019_candidate_operational_state"
down_revision = "0018_reservation_episodes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    operational_status = sa.Enum(
        "SCHEDULED",
        "DELAYED",
        "BOARDING",
        "DEPARTED_ORIGIN",
        "CANCELLED",
        "UNKNOWN",
        name="operationalstatus",
        native_enum=False,
    )
    booking_window_status = sa.Enum(
        "OPEN",
        "WAITLIST",
        "CLOSED",
        "UNKNOWN",
        name="bookingwindowstatus",
        native_enum=False,
    )
    with op.batch_alter_table("watch_candidates") as batch_op:
        batch_op.add_column(
            sa.Column("scheduled_departure_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("estimated_departure_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("actual_departure_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("delay_minutes", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "operational_status",
                operational_status,
                nullable=False,
                server_default="UNKNOWN",
            )
        )
        batch_op.add_column(
            sa.Column(
                "booking_window_status",
                booking_window_status,
                nullable=False,
                server_default="UNKNOWN",
            )
        )
        batch_op.add_column(sa.Column("operational_source", sa.String(length=80), nullable=True))
        batch_op.add_column(
            sa.Column("operational_observed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("operational_fresh_until", sa.DateTime(timezone=True), nullable=True)
        )

    op.execute("UPDATE watch_candidates SET scheduled_departure_at = departure_at")

    with op.batch_alter_table("watch_candidates") as batch_op:
        batch_op.alter_column("scheduled_departure_at", nullable=False)
        batch_op.alter_column("operational_status", server_default=None)
        batch_op.alter_column("booking_window_status", server_default=None)
        batch_op.create_check_constraint(
            "ck_watch_candidate_delay_minutes_nonnegative",
            "delay_minutes IS NULL OR delay_minutes >= 0",
        )
        batch_op.create_check_constraint(
            "ck_watch_candidate_operational_provenance_shape",
            "operational_source IS NULL OR (operational_observed_at IS NOT NULL "
            "AND operational_fresh_until IS NOT NULL "
            "AND operational_fresh_until >= operational_observed_at)",
        )
        batch_op.create_check_constraint(
            "ck_watch_candidate_operational_provenance_absent_shape",
            "operational_source IS NOT NULL OR (operational_observed_at IS NULL "
            "AND operational_fresh_until IS NULL)",
        )
        batch_op.create_check_constraint(
            "ck_watch_candidate_operational_source_nonempty",
            "operational_source IS NULL OR length(trim(operational_source)) > 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("watch_candidates") as batch_op:
        batch_op.drop_constraint("ck_watch_candidate_operational_source_nonempty", type_="check")
        batch_op.drop_constraint(
            "ck_watch_candidate_operational_provenance_absent_shape", type_="check"
        )
        batch_op.drop_constraint("ck_watch_candidate_operational_provenance_shape", type_="check")
        batch_op.drop_constraint("ck_watch_candidate_delay_minutes_nonnegative", type_="check")
        batch_op.drop_column("operational_fresh_until")
        batch_op.drop_column("operational_observed_at")
        batch_op.drop_column("operational_source")
        batch_op.drop_column("booking_window_status")
        batch_op.drop_column("operational_status")
        batch_op.drop_column("delay_minutes")
        batch_op.drop_column("actual_departure_at")
        batch_op.drop_column("estimated_departure_at")
        batch_op.drop_column("scheduled_departure_at")
