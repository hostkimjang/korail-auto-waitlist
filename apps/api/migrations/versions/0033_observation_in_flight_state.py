"""Separate watch observation scheduling from the expiring in-flight claim."""

import sqlalchemy as sa
from alembic import op

revision = "0033_observation_in_flight"
down_revision = "0032_manual_reservation_rearm"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "watches",
        sa.Column("observation_in_flight_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("watches", "observation_in_flight_until")
