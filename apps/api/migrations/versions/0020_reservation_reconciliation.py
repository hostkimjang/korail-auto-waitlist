"""Persist secret-free reservation confirmation and reconciliation evidence."""

import sqlalchemy as sa
from alembic import op

revision = "0020_reservation_reconciliation"
down_revision = "0019_candidate_operational_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    confirmation_outcome = sa.Enum(
        "CONFIRMED_PAYMENT_REQUIRED",
        "NOT_FOUND",
        "AUTH_REQUIRED",
        "PROVIDER_BLOCKED",
        "INCONCLUSIVE",
        name="reservationconfirmationoutcome",
        native_enum=False,
    )
    with op.batch_alter_table("reservation_attempts") as batch_op:
        batch_op.add_column(sa.Column("credential_version", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("confirmation_outcome", confirmation_outcome, nullable=True)
        )
        batch_op.add_column(
            sa.Column("confirmation_source", sa.String(length=80), nullable=True)
        )
        batch_op.add_column(
            sa.Column("confirmation_observed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_check_constraint(
            "ck_reservation_attempt_credential_version_positive",
            "credential_version IS NULL OR credential_version >= 1",
        )
        batch_op.create_check_constraint(
            "ck_reservation_attempt_confirmation_source_nonempty",
            "confirmation_source IS NULL OR length(trim(confirmation_source)) > 0",
        )
        batch_op.create_check_constraint(
            "ck_reservation_attempt_confirmation_provenance_shape",
            "(confirmation_outcome IS NULL AND confirmation_source IS NULL "
            "AND confirmation_observed_at IS NULL) OR "
            "(confirmation_outcome IS NOT NULL AND confirmation_source IS NOT NULL "
            "AND confirmation_observed_at IS NOT NULL)",
        )
        batch_op.create_check_constraint(
            "ck_reservation_attempt_reconciliation_timestamp_order",
            "last_reconciled_at IS NULL OR (confirmation_observed_at IS NOT NULL "
            "AND last_reconciled_at >= confirmation_observed_at)",
        )


def downgrade() -> None:
    with op.batch_alter_table("reservation_attempts") as batch_op:
        batch_op.drop_constraint(
            "ck_reservation_attempt_reconciliation_timestamp_order", type_="check"
        )
        batch_op.drop_constraint(
            "ck_reservation_attempt_confirmation_provenance_shape", type_="check"
        )
        batch_op.drop_constraint(
            "ck_reservation_attempt_confirmation_source_nonempty", type_="check"
        )
        batch_op.drop_constraint(
            "ck_reservation_attempt_credential_version_positive", type_="check"
        )
        batch_op.drop_column("last_reconciled_at")
        batch_op.drop_column("confirmation_observed_at")
        batch_op.drop_column("confirmation_source")
        batch_op.drop_column("confirmation_outcome")
        batch_op.drop_column("credential_version")
