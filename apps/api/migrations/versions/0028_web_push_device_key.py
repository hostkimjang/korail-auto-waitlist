"""Add a unique, non-secret identity for each Web Push subscription."""

import sqlalchemy as sa
from alembic import op

revision = "0028_web_push_device_key"
down_revision = "0027_native_push_pairing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notification_channels",
        sa.Column("web_push_device_key", sa.String(length=43), nullable=True),
    )
    op.create_index(
        "ix_notification_channels_web_push_device_key",
        "notification_channels",
        ["web_push_device_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_channels_web_push_device_key",
        table_name="notification_channels",
    )
    op.drop_column("notification_channels", "web_push_device_key")
