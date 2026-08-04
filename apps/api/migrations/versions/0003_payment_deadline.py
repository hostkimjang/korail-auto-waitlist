"""Add optional payment deadline used by payment-required UX."""

import sqlalchemy as sa
from alembic import op

revision = "0003_payment_deadline"
down_revision = "0002_admin_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("watches", sa.Column("payment_deadline", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("watches", "payment_deadline")
