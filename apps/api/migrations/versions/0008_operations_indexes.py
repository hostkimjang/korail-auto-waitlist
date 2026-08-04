"""Add time-leading indexes for the durable operations summary."""

from alembic import op

revision = "0008_operations_indexes"
down_revision = "0007_station_catalog_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_seat_observations_observed_at",
        "seat_observations",
        ["observed_at"],
    )
    op.create_index(
        "ix_reservation_attempts_started_at",
        "reservation_attempts",
        ["started_at"],
    )
    op.create_index(
        "ix_watch_transition_history_created_at",
        "watch_transition_history",
        ["created_at"],
    )
    op.create_index(
        "ix_outbox_events_processed_at",
        "outbox_events",
        ["processed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_events_processed_at", table_name="outbox_events")
    op.drop_index(
        "ix_watch_transition_history_created_at",
        table_name="watch_transition_history",
    )
    op.drop_index(
        "ix_reservation_attempts_started_at",
        table_name="reservation_attempts",
    )
    op.drop_index(
        "ix_seat_observations_observed_at",
        table_name="seat_observations",
    )
