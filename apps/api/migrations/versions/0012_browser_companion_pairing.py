"""Add one-time browser companion pairing and replay-safe challenges."""

import sqlalchemy as sa
from alembic import op

revision = "0012_browser_companion_pairing"
down_revision = "0011_korail_browser_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "browser_companion_pairings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_browser_companion_pairings_code_hash",
        "browser_companion_pairings",
        ["code_hash"],
        unique=True,
    )
    op.create_index(
        "ix_browser_companion_pairings_expires_at",
        "browser_companion_pairings",
        ["expires_at"],
    )

    op.create_table(
        "browser_companion_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("extension_origin", sa.String(100), nullable=False),
        sa.Column("client_id", sa.String(36), nullable=False),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_in_window", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_browser_companion_credentials_token_hash",
        "browser_companion_credentials",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_browser_companion_credentials_revoked_at",
        "browser_companion_credentials",
        ["revoked_at"],
    )
    op.create_index(
        "ix_browser_companion_credential_installation",
        "browser_companion_credentials",
        ["extension_origin", "client_id"],
    )

    op.create_table(
        "browser_companion_challenges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "credential_id",
            sa.String(36),
            sa.ForeignKey("browser_companion_credentials.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("challenge_hash", sa.String(64), nullable=False),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("path", sa.String(160), nullable=False),
        sa.Column("body_sha256", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_browser_companion_challenges_challenge_hash",
        "browser_companion_challenges",
        ["challenge_hash"],
        unique=True,
    )
    op.create_index(
        "ix_browser_companion_challenges_expires_at",
        "browser_companion_challenges",
        ["expires_at"],
    )
    op.create_index(
        "ix_browser_companion_challenge_active",
        "browser_companion_challenges",
        ["credential_id", "expires_at", "consumed_at"],
    )

    with op.batch_alter_table("korail_browser_snapshot_batches") as batch_op:
        batch_op.add_column(sa.Column("credential_id", sa.String(36), nullable=True))
        batch_op.add_column(sa.Column("challenge_id", sa.String(36), nullable=True))
        batch_op.create_foreign_key(
            "fk_korail_browser_snapshot_batch_credential",
            "browser_companion_credentials",
            ["credential_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_unique_constraint(
            "uq_korail_browser_snapshot_batch_challenge",
            ["challenge_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("korail_browser_snapshot_batches") as batch_op:
        batch_op.drop_constraint("uq_korail_browser_snapshot_batch_challenge", type_="unique")
        batch_op.drop_constraint("fk_korail_browser_snapshot_batch_credential", type_="foreignkey")
        batch_op.drop_column("challenge_id")
        batch_op.drop_column("credential_id")
    op.drop_table("browser_companion_challenges")
    op.drop_table("browser_companion_credentials")
    op.drop_table("browser_companion_pairings")
