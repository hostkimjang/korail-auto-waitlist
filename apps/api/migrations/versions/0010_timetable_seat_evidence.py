"""Persist immutable server-issued timetable seat registration evidence."""

import sqlalchemy as sa
from alembic import op

revision = "0010_timetable_seat_evidence"
down_revision = "0009_official_page_confirmations"
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
        "timetable_seat_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column("provider", provider, nullable=False),
        sa.Column("origin_node_id", sa.String(80), nullable=False),
        sa.Column("destination_node_id", sa.String(80), nullable=False),
        sa.Column("canonical_train_number", sa.String(40), nullable=False),
        sa.Column("departure_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("passenger_count", sa.Integer(), nullable=False),
        sa.Column("seat_class", seat_class, nullable=False),
        sa.Column("status", seat_status, nullable=False),
        sa.Column("provenance_kind", sa.String(40), nullable=False),
        sa.Column("source", sa.String(80)),
        sa.Column("observed_at", sa.DateTime(timezone=True)),
        sa.Column("fresh_until", sa.DateTime(timezone=True)),
        sa.Column("reason", sa.String(80)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("registration_valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("evidence_hash", name="uq_timetable_seat_evidence_hash"),
        sa.CheckConstraint(
            "provider IN ('KORAIL', 'SRT')", name="ck_timetable_seat_evidence_provider"
        ),
        sa.CheckConstraint(
            "seat_class IN ('STANDARD', 'FIRST')",
            name="ck_timetable_seat_evidence_seat_class",
        ),
        sa.CheckConstraint(
            "passenger_count BETWEEN 1 AND 9",
            name="ck_timetable_seat_evidence_passenger_count",
        ),
        sa.CheckConstraint(
            "provenance_kind IN ('not_observed', 'official_provider', "
            "'user_confirmed_official_page')",
            name="ck_timetable_seat_evidence_provenance_kind",
        ),
        sa.CheckConstraint(
            "((provenance_kind = 'not_observed' AND status = 'UNKNOWN' "
            "AND reason IS NOT NULL AND source IS NULL AND observed_at IS NULL "
            "AND fresh_until IS NULL) OR "
            "(provenance_kind <> 'not_observed' AND source IS NOT NULL "
            "AND observed_at IS NOT NULL AND reason IS NULL))",
            name="ck_timetable_seat_evidence_provenance_shape",
        ),
        sa.CheckConstraint(
            "(provenance_kind <> 'user_confirmed_official_page' OR "
            "(source = 'official-page-user-confirmation' "
            "AND fresh_until IS NOT NULL AND fresh_until > observed_at))",
            name="ck_timetable_seat_evidence_user_confirmation",
        ),
        sa.CheckConstraint(
            "registration_valid_until > created_at",
            name="ck_timetable_seat_evidence_registration_window",
        ),
    )
    op.create_index(
        "ix_timetable_seat_evidence_identity",
        "timetable_seat_evidence",
        [
            "provider",
            "origin_node_id",
            "destination_node_id",
            "departure_at",
            "passenger_count",
            "seat_class",
        ],
    )
    with op.batch_alter_table("watch_candidates") as batch_op:
        batch_op.add_column(sa.Column("registration_evidence_id", sa.String(36)))
        batch_op.create_foreign_key(
            "fk_watch_candidate_registration_evidence",
            "timetable_seat_evidence",
            ["registration_evidence_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index(
        "ix_watch_candidates_registration_evidence_id",
        "watch_candidates",
        ["registration_evidence_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_watch_candidates_registration_evidence_id", table_name="watch_candidates"
    )
    with op.batch_alter_table("watch_candidates") as batch_op:
        batch_op.drop_constraint(
            "fk_watch_candidate_registration_evidence", type_="foreignkey"
        )
        batch_op.drop_column("registration_evidence_id")
    op.drop_table("timetable_seat_evidence")
