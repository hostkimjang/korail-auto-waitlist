"""Add fenced provider/account execution leases."""

import sqlalchemy as sa
from alembic import op

revision = "0015_execution_lease"
down_revision = "0014_evidence_eligibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_execution_leases",
        sa.Column("provider", sa.String(6), primary_key=True),
        sa.Column("account_scope", sa.String(128), primary_key=True),
        sa.Column("owner_token", sa.String(128), nullable=True),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "provider IN ('KORAIL', 'SRT')",
            name="ck_provider_execution_lease_provider_allowed",
        ),
        sa.CheckConstraint(
            "length(trim(account_scope)) > 0",
            name="ck_provider_execution_lease_scope_nonempty",
        ),
        sa.CheckConstraint(
            "fencing_token >= 1",
            name="ck_provider_execution_lease_fencing_positive",
        ),
        sa.CheckConstraint(
            "((owner_token IS NULL AND expires_at IS NULL) OR "
            "(owner_token IS NOT NULL AND expires_at IS NOT NULL))",
            name="ck_provider_execution_lease_owner_expiry_shape",
        ),
    )
    op.create_index(
        "ix_provider_execution_leases_expires_at",
        "provider_execution_leases",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_table("provider_execution_leases")
