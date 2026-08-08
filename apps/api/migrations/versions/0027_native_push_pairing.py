"""Add one-time native push pairing and scoped device credentials."""

import sqlalchemy as sa
from alembic import op

revision = "0027_native_push_pairing"
down_revision = "0026_unified_observation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    notification_kind = sa.Enum(
        "WEB_PUSH",
        "ANDROID_FCM",
        "IOS_APNS",
        "TELEGRAM",
        "DISCORD_WEBHOOK",
        "GENERIC_WEBHOOK",
        name="notificationkind",
        native_enum=False,
    )
    op.create_table(
        "native_push_pairings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("kind", notification_kind, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_native_push_pairings_code_hash",
        "native_push_pairings",
        ["code_hash"],
        unique=True,
    )
    op.create_index(
        "ix_native_push_pairings_expires_at",
        "native_push_pairings",
        ["expires_at"],
    )
    op.create_table(
        "native_push_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "channel_id",
            sa.String(36),
            sa.ForeignKey("notification_channels.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("kind", notification_kind, nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_native_push_credentials_token_hash",
        "native_push_credentials",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_native_push_credentials_channel_id",
        "native_push_credentials",
        ["channel_id"],
        unique=True,
    )
    op.create_index(
        "ix_native_push_credentials_revoked_at",
        "native_push_credentials",
        ["revoked_at"],
    )


def downgrade() -> None:
    op.drop_table("native_push_credentials")
    op.drop_table("native_push_pairings")
