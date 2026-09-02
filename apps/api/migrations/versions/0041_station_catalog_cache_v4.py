"""Align the station catalog cache default with reviewed identity supplements."""

import sqlalchemy as sa
from alembic import op

revision = "0041_station_cache_v4"
down_revision = "0040_legacy_failed_unknown"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing v2/v3 rows deliberately keep their version so the application treats
    # their omission-prone snapshots as stale and recollects them under schema v4.
    with op.batch_alter_table("station_catalog_cache") as batch_op:
        batch_op.alter_column(
            "schema_version",
            existing_type=sa.Integer(),
            existing_nullable=False,
            server_default="4",
        )


def downgrade() -> None:
    with op.batch_alter_table("station_catalog_cache") as batch_op:
        batch_op.alter_column(
            "schema_version",
            existing_type=sa.Integer(),
            existing_nullable=False,
            server_default="2",
        )
