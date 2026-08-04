"""Initial single-admin service schema."""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    provider = sa.Enum("KORAIL", "SRT", "MOCK", name="provider", native_enum=False)
    watch_status = sa.Enum(
        "DRAFT",
        "SCHEDULED",
        "WATCHING",
        "OFFICIAL_WAITLIST",
        "SEAT_FOUND",
        "RESERVING",
        "PAYMENT_REQUIRED",
        "COMPLETED",
        "PAUSED",
        "COOLDOWN",
        "AUTH_REQUIRED",
        "EXPIRED",
        "FAILED",
        name="watchstatus",
        native_enum=False,
    )
    channel_kind = sa.Enum(
        "WEB_PUSH",
        "TELEGRAM",
        "DISCORD_WEBHOOK",
        "GENERIC_WEBHOOK",
        name="notificationkind",
        native_enum=False,
    )
    outbox_status = sa.Enum("PENDING", "SENT", "FAILED", name="outboxstatus", native_enum=False)

    op.create_table(
        "watches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider", provider, nullable=False),
        sa.Column("origin", sa.String(40), nullable=False),
        sa.Column("destination", sa.String(40), nullable=False),
        sa.Column("travel_date", sa.Date(), nullable=False),
        sa.Column("time_from", sa.Time(), nullable=False),
        sa.Column("time_to", sa.Time(), nullable=False),
        sa.Column("seat_class", sa.String(20), nullable=False),
        sa.Column("passenger_count", sa.Integer(), nullable=False),
        sa.Column("train_numbers", sa.JSON(), nullable=False),
        sa.Column("notification_channel_ids", sa.JSON(), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("status", watch_status, nullable=False),
        sa.Column("dedupe_key", sa.String(64), nullable=False),
        sa.Column("next_check_at", sa.DateTime(timezone=True)),
        sa.Column("cooldown_until", sa.DateTime(timezone=True)),
        sa.Column("reservation_attempted", sa.Boolean(), nullable=False),
        sa.Column("official_booking_url", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_watches_provider", "watches", ["provider"])
    op.create_index("ix_watches_status", "watches", ["status"])
    op.create_index("ix_watches_dedupe_key", "watches", ["dedupe_key"])

    op.create_table(
        "notification_channels",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", channel_kind, nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("config_ciphertext", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("aggregate_type", sa.String(40), nullable=False),
        sa.Column("aggregate_id", sa.String(36), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("dedupe_key", sa.String(128), nullable=False, unique=True),
        sa.Column("status", outbox_status, nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(240)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_outbox_events_aggregate_id", "outbox_events", ["aggregate_id"])
    op.create_index("ix_outbox_events_event_type", "outbox_events", ["event_type"])
    op.create_index("ix_outbox_events_status", "outbox_events", ["status"])
    op.create_index("ix_outbox_events_created_at", "outbox_events", ["created_at"])

    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scope", sa.String(100), nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("resource_id", sa.String(36), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scope", "key", name="uq_idempotency_scope_key"),
    )


def downgrade() -> None:
    op.drop_table("idempotency_records")
    op.drop_table("outbox_events")
    op.drop_table("notification_channels")
    op.drop_table("watches")
