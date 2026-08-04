"""Add optional payment deadline used by payment-required UX."""

from alembic import op
import sqlalchemy as sa


revision = "0003_payment_deadline"
down_revision = "0002_admin_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("watches", sa.Column("payment_deadline", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("watches", "payment_deadline")
