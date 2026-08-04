"""Add identity-keyed user confirmations from official seat pages."""

import sqlalchemy as sa
from alembic import op

revision = "0009_official_page_confirmations"
down_revision = "0008_operations_indexes"
branch_labels = None
depends_on = None


provider = sa.Enum("KORAIL", "SRT", "MOCK", name="provider", native_enum=False)
seat_class = sa.Enum(
    "STANDARD",
    "FIRST",
    "INFANT",
    "FREE",
    "WAITLIST",
    "ANY",
    name="seatclass",
    native_enum=False,
)
seat_status = sa.Enum(
    "UNAVAILABLE",
    "UNKNOWN",
    "AVAILABLE",
    "LIMITED",
    "STANDING_PLUS_SEAT",
    "NOT_ENOUGH_SEATS",
    "SOLD_OUT",
    "WAITLIST_AVAILABLE",
    "RESERVATION_COMPLETED",
    "NOT_OFFERED",
    "DEPARTED",
    "OUT_OF_SERVICE",
    "STALE",
    "ERROR",
    name="seatobservationstatus",
    native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        "official_page_seat_confirmations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("provider", provider, nullable=False),
        sa.Column("origin_node_id", sa.String(80), nullable=False),
        sa.Column("destination_node_id", sa.String(80), nullable=False),
        sa.Column("train_number", sa.String(40), nullable=False),
        sa.Column("departure_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("passenger_count", sa.Integer(), nullable=False),
        sa.Column("seat_class", seat_class, nullable=False),
        sa.Column("status", seat_status, nullable=False),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fresh_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "batch_id",
            "seat_class",
            name="uq_official_page_confirmation_batch_seat_class",
        ),
        sa.CheckConstraint(
            "provider IN ('KORAIL', 'SRT')", name="ck_official_page_confirmation_provider"
        ),
        sa.CheckConstraint(
            "seat_class IN ('STANDARD', 'FIRST')",
            name="ck_official_page_confirmation_seat_class",
        ),
        sa.CheckConstraint(
            "status IN ('AVAILABLE', 'SOLD_OUT', 'WAITLIST_AVAILABLE', 'NOT_OFFERED')",
            name="ck_official_page_confirmation_status",
        ),
        sa.CheckConstraint(
            "source = 'official-page-user-confirmation'",
            name="ck_official_page_confirmation_source",
        ),
        sa.CheckConstraint(
            "fresh_until > observed_at",
            name="ck_official_page_confirmation_freshness_order",
        ),
        sa.CheckConstraint(
            "passenger_count BETWEEN 1 AND 9",
            name="ck_official_page_confirmation_passenger_count",
        ),
    )
    op.create_index(
        "ix_official_page_confirmation_route_fresh",
        "official_page_seat_confirmations",
        [
            "provider",
            "origin_node_id",
            "destination_node_id",
            "passenger_count",
            "departure_at",
            "fresh_until",
            "observed_at",
        ],
    )
    op.create_index(
        "ix_official_page_confirmation_batch_id",
        "official_page_seat_confirmations",
        ["batch_id"],
    )


def downgrade() -> None:
    op.drop_table("official_page_seat_confirmations")
