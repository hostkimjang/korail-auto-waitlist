"""Store append-only KORAIL browser companion snapshot batches."""

import sqlalchemy as sa
from alembic import op

revision = "0011_korail_browser_snapshots"
down_revision = "0010_timetable_seat_evidence"
branch_labels = None
depends_on = None

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
        "korail_browser_snapshot_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("origin", sa.String(40), nullable=False),
        sa.Column("destination", sa.String(40), nullable=False),
        sa.Column("travel_date", sa.Date(), nullable=False),
        sa.Column("passenger_count", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fresh_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source = 'korail-official-browser-companion'",
            name="ck_korail_browser_snapshot_batch_source",
        ),
        sa.CheckConstraint(
            "passenger_count BETWEEN 1 AND 9",
            name="ck_korail_browser_snapshot_batch_passenger_count",
        ),
        sa.CheckConstraint(
            "fresh_until > observed_at",
            name="ck_korail_browser_snapshot_batch_freshness_order",
        ),
    )
    op.create_index(
        "ix_korail_browser_snapshot_batch_route_fresh",
        "korail_browser_snapshot_batches",
        [
            "origin",
            "destination",
            "travel_date",
            "passenger_count",
            "fresh_until",
            "observed_at",
        ],
    )
    op.create_table(
        "korail_browser_seat_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "batch_id",
            sa.String(36),
            sa.ForeignKey("korail_browser_snapshot_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("train_number", sa.String(40), nullable=False),
        sa.Column("departure_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seat_class", seat_class, nullable=False),
        sa.Column("status", seat_status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "batch_id",
            "train_number",
            "seat_class",
            name="uq_korail_browser_snapshot_batch_train_seat",
        ),
        sa.CheckConstraint(
            "seat_class IN ('STANDARD', 'FIRST')",
            name="ck_korail_browser_snapshot_seat_class",
        ),
        sa.CheckConstraint(
            "status IN ('AVAILABLE', 'LIMITED', 'SOLD_OUT', "
            "'WAITLIST_AVAILABLE', 'NOT_OFFERED')",
            name="ck_korail_browser_snapshot_status",
        ),
    )
    op.create_index(
        "ix_korail_browser_snapshot_identity",
        "korail_browser_seat_snapshots",
        ["train_number", "departure_at", "seat_class"],
    )
    with op.batch_alter_table("timetable_seat_evidence") as batch_op:
        batch_op.drop_constraint(
            "ck_timetable_seat_evidence_provenance_kind", type_="check"
        )
        batch_op.create_check_constraint(
            "ck_timetable_seat_evidence_provenance_kind",
            "provenance_kind IN ('not_observed', 'official_provider', "
            "'official_page_browser_companion', 'user_confirmed_official_page')",
        )
        batch_op.create_check_constraint(
            "ck_timetable_seat_evidence_browser_companion",
            "(provenance_kind <> 'official_page_browser_companion' OR "
            "(source = 'korail-official-browser-companion' "
            "AND fresh_until IS NOT NULL AND fresh_until > observed_at))",
        )


def downgrade() -> None:
    with op.batch_alter_table("timetable_seat_evidence") as batch_op:
        batch_op.drop_constraint(
            "ck_timetable_seat_evidence_browser_companion", type_="check"
        )
        batch_op.drop_constraint(
            "ck_timetable_seat_evidence_provenance_kind", type_="check"
        )
        batch_op.create_check_constraint(
            "ck_timetable_seat_evidence_provenance_kind",
            "provenance_kind IN ('not_observed', 'official_provider', "
            "'user_confirmed_official_page')",
        )
    op.drop_table("korail_browser_seat_snapshots")
    op.drop_table("korail_browser_snapshot_batches")
