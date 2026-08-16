"""Persist a provider-neutral reason for every reservation result."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0035_reservation_result_reason"
down_revision = "0034_progress_terminal_time"
branch_labels = None
depends_on = None

_REASON_NAMES = (
    "RESERVATION_PENDING",
    "PAYMENT_HOLD_CREATED",
    "TARGET_NOT_AVAILABLE",
    "TARGET_AMBIGUOUS",
    "SEAT_NOT_AVAILABLE",
    "RESERVATION_CONTROL_UNAVAILABLE",
    "SEAT_SELECTION_LOST",
    "DELAY_CONSENT_REQUIRED",
    "EXISTING_RESERVATION_ACTION_REQUIRED",
    "PROVIDER_NOTICE_ACTION_REQUIRED",
    "AUTHENTICATION_REQUIRED",
    "PROVIDER_BLOCKED",
    "PROVIDER_UNAVAILABLE",
    "PROVIDER_RESPONSE_INVALID",
    "RESERVATION_REQUEST_RESULT_UNKNOWN",
    "RESERVATION_FAILED",
)


def upgrade() -> None:
    reason_type = sa.Enum(
        *_REASON_NAMES,
        name="reservationresultreasoncode",
        native_enum=False,
    )
    with op.batch_alter_table("reservation_attempts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "result_reason_code",
                reason_type,
                nullable=False,
                server_default="RESERVATION_PENDING",
            )
        )

    op.execute(
        sa.text(
            "UPDATE reservation_attempts SET result_reason_code = CASE outcome "
            "WHEN 'PAYMENT_REQUIRED' THEN 'PAYMENT_HOLD_CREATED' "
            "WHEN 'RESERVED' THEN 'PAYMENT_HOLD_CREATED' "
            "WHEN 'NOT_AVAILABLE' THEN 'TARGET_NOT_AVAILABLE' "
            "WHEN 'AUTH_REQUIRED' THEN 'AUTHENTICATION_REQUIRED' "
            "WHEN 'PROVIDER_BLOCKED' THEN 'PROVIDER_BLOCKED' "
            "WHEN 'FAILED' THEN 'RESERVATION_FAILED' "
            "WHEN 'UNKNOWN' THEN 'RESERVATION_REQUEST_RESULT_UNKNOWN' "
            "ELSE 'RESERVATION_PENDING' END"
        )
    )

    allowed = ", ".join(f"'{name}'" for name in _REASON_NAMES)
    with op.batch_alter_table("reservation_attempts") as batch_op:
        batch_op.alter_column("result_reason_code", server_default=None)
        batch_op.create_check_constraint(
            "ck_reservation_attempt_result_reason_code_allowed",
            f"result_reason_code IN ({allowed})",
        )


def downgrade() -> None:
    with op.batch_alter_table("reservation_attempts") as batch_op:
        batch_op.drop_constraint(
            "ck_reservation_attempt_result_reason_code_allowed",
            type_="check",
        )
        batch_op.drop_column("result_reason_code")
