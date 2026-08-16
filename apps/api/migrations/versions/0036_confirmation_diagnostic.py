"""Persist provider-neutral diagnostics for inconclusive official confirmation."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0036_confirmation_diagnostic"
down_revision = "0035_reservation_result_reason"
branch_labels = None
depends_on = None

_DIAGNOSTIC_NAMES = (
    "OFFICIAL_READ_UNAVAILABLE",
    "CREDENTIAL_CONTEXT_MISMATCH",
    "OFFICIAL_RECORD_AMBIGUOUS",
    "OFFICIAL_EVIDENCE_INSUFFICIENT",
    "UNSPECIFIED",
)
_ALLOWED_CONSTRAINT_NAME = "ck_reservation_attempt_confirm_diag_allowed"
_INCONCLUSIVE_CONSTRAINT_NAME = "ck_reservation_attempt_confirm_diag_inconclusive"


def upgrade() -> None:
    diagnostic_type = sa.Enum(
        *_DIAGNOSTIC_NAMES,
        name="reservationconfirmationdiagnosticcode",
        native_enum=False,
    )
    with op.batch_alter_table("reservation_attempts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "confirmation_diagnostic_code",
                diagnostic_type,
                nullable=True,
            )
        )

    op.execute(
        sa.text(
            "UPDATE reservation_attempts SET confirmation_diagnostic_code = 'UNSPECIFIED' "
            "WHERE confirmation_outcome = 'INCONCLUSIVE'"
        )
    )

    allowed = ", ".join(f"'{name}'" for name in _DIAGNOSTIC_NAMES)
    with op.batch_alter_table("reservation_attempts") as batch_op:
        batch_op.create_check_constraint(
            _ALLOWED_CONSTRAINT_NAME,
            f"confirmation_diagnostic_code IS NULL OR confirmation_diagnostic_code IN ({allowed})",
        )
        batch_op.create_check_constraint(
            _INCONCLUSIVE_CONSTRAINT_NAME,
            "confirmation_diagnostic_code IS NULL OR confirmation_outcome = 'INCONCLUSIVE'",
        )


def downgrade() -> None:
    with op.batch_alter_table("reservation_attempts") as batch_op:
        batch_op.drop_constraint(
            _INCONCLUSIVE_CONSTRAINT_NAME,
            type_="check",
        )
        batch_op.drop_constraint(
            _ALLOWED_CONSTRAINT_NAME,
            type_="check",
        )
        batch_op.drop_column("confirmation_diagnostic_code")
