"""Add the durable canonical TAGO station catalog snapshot."""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0007_station_catalog_cache"
down_revision = "0006_admin_password_auth"
branch_labels = None
depends_on = None

CANONICAL_KEY = "tago_station_catalog_all"


def upgrade() -> None:
    table = op.create_table(
        "station_catalog_cache",
        sa.Column("cache_key", sa.String(40), primary_key=True),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("payload", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("station_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refresh_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refresh_owner", sa.String(64), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_category", sa.String(64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"cache_key = '{CANONICAL_KEY}'",
            name="ck_station_catalog_cache_canonical_key",
        ),
        sa.CheckConstraint(
            "schema_version >= 1",
            name="ck_station_catalog_cache_schema_version_positive",
        ),
        sa.CheckConstraint(
            "station_count >= 0",
            name="ck_station_catalog_cache_count_nonnegative",
        ),
        sa.CheckConstraint(
            "payload IS NULL OR station_count > 0",
            name="ck_station_catalog_cache_payload_nonempty",
        ),
        sa.CheckConstraint(
            "payload IS NULL OR (retrieved_at IS NOT NULL AND refresh_after IS NOT NULL)",
            name="ck_station_catalog_cache_payload_timestamps",
        ),
        sa.CheckConstraint(
            "refresh_owner IS NULL OR length(trim(refresh_owner)) > 0",
            name="ck_station_catalog_cache_owner_nonempty",
        ),
        sa.CheckConstraint(
            "last_error_category IS NULL OR length(trim(last_error_category)) > 0",
            name="ck_station_catalog_cache_error_nonempty",
        ),
    )
    op.bulk_insert(
        table,
        [
            {
                "cache_key": CANONICAL_KEY,
                "schema_version": 2,
                "payload": None,
                "station_count": 0,
                "retrieved_at": None,
                "refresh_after": None,
                "refresh_owner": None,
                "lease_until": None,
                "last_attempt_at": None,
                "last_error_category": None,
                "updated_at": datetime.now(UTC),
            }
        ],
    )


def downgrade() -> None:
    op.drop_table("station_catalog_cache")
