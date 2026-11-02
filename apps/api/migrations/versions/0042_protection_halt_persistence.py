"""Persist the provider automation-protection keepalive halt per account revision.

The halt binds to one account revision (credential generation plus the updated_at
micros at verdict time) so a restart resumes the fence instead of spending a
fresh login attempt, and any newer authentication write expires it.
"""

import sqlalchemy as sa
from alembic import op

revision = "0042_protection_halt"
down_revision = "0041_station_cache_v4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("rail_provider_accounts") as batch_op:
        batch_op.add_column(
            sa.Column("protection_halt_credential_version", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("protection_halt_revision_micros", sa.BigInteger(), nullable=True)
        )
        batch_op.create_check_constraint(
            "ck_rail_provider_account_protection_halt_pair",
            "(protection_halt_credential_version IS NULL) = "
            "(protection_halt_revision_micros IS NULL)",
        )


def downgrade() -> None:
    with op.batch_alter_table("rail_provider_accounts") as batch_op:
        batch_op.drop_constraint(
            "ck_rail_provider_account_protection_halt_pair",
            type_="check",
        )
        batch_op.drop_column("protection_halt_revision_micros")
        batch_op.drop_column("protection_halt_credential_version")
