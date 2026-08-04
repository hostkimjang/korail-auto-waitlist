"""Persist official station identities and exact watch candidates."""

import sqlalchemy as sa
from alembic import op

revision = "0004_watch_candidates"
down_revision = "0003_payment_deadline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("watches", sa.Column("origin_node_id", sa.String(80)))
    op.add_column("watches", sa.Column("destination_node_id", sa.String(80)))
    op.create_table(
        "watch_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "watch_id",
            sa.String(36),
            sa.ForeignKey("watches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("train_number", sa.String(40), nullable=False),
        sa.Column("departure_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("arrival_at", sa.DateTime(timezone=True)),
        sa.Column("seat_class", sa.String(20), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.CheckConstraint("priority >= 1", name="ck_watch_candidate_priority_positive"),
        sa.UniqueConstraint(
            "watch_id",
            "train_number",
            "departure_at",
            "seat_class",
            name="uq_watch_candidate_identity",
        ),
        sa.UniqueConstraint("watch_id", "priority", name="uq_watch_candidate_priority"),
    )
    op.create_index("ix_watch_candidates_watch_id", "watch_candidates", ["watch_id"])


def downgrade() -> None:
    op.drop_table("watch_candidates")
    op.drop_column("watches", "destination_node_id")
    op.drop_column("watches", "origin_node_id")
