"""Add the singleton username/password administrator account.

Legacy WebAuthn tables are intentionally retained so upgrades are reversible and do not
destroy credential material. The runtime no longer reads them; an upgraded installation
must complete the one-time username/password registration screen.
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_admin_password_auth"
down_revision = "0005_persistence_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Sessions issued by the removed passkey/recovery flow have no password-account
    # provenance. Force one clean registration instead of carrying them across auth modes.
    op.execute("DELETE FROM admin_sessions")
    op.create_table(
        "admin_accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("singleton_slot", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("singleton_slot = 1", name="ck_admin_account_singleton_slot"),
        sa.CheckConstraint(
            "length(trim(username)) >= 3", name="ck_admin_account_username_nonempty"
        ),
        sa.UniqueConstraint("singleton_slot", name="uq_admin_accounts_singleton_slot"),
        sa.UniqueConstraint("username", name="uq_admin_accounts_username"),
    )


def downgrade() -> None:
    op.drop_table("admin_accounts")
