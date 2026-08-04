"""Add single-admin WebAuthn, recovery code, and session state."""

from alembic import op
import sqlalchemy as sa


revision = "0002_admin_auth"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("singleton_slot", sa.Integer(), nullable=False, unique=True),
        sa.Column("credential_id", sa.Text(), nullable=False, unique=True),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False),
        sa.Column("transports", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "admin_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_admin_sessions_token_hash", "admin_sessions", ["token_hash"], unique=True)
    op.create_index("ix_admin_sessions_expires_at", "admin_sessions", ["expires_at"])
    op.create_table(
        "recovery_codes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_recovery_codes_code_hash", "recovery_codes", ["code_hash"], unique=True)
    op.create_table(
        "auth_challenges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("challenge", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_challenges_kind", "auth_challenges", ["kind"])
    op.create_index("ix_auth_challenges_expires_at", "auth_challenges", ["expires_at"])


def downgrade() -> None:
    op.drop_table("auth_challenges")
    op.drop_table("recovery_codes")
    op.drop_table("admin_sessions")
    op.drop_table("admin_credentials")
