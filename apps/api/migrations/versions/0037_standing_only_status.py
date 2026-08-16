"""Represent official standing-only inventory without claiming a seated ticket."""

from __future__ import annotations

from alembic import op

revision = "0037_standing_only_status"
down_revision = "0036_confirmation_diagnostic"
branch_labels = None
depends_on = None

_OBSERVATION_CONSTRAINT = "ck_seat_observation_status_allowed"
_BROWSER_SNAPSHOT_CONSTRAINT = "ck_korail_browser_snapshot_status"

_OBSERVATION_STATUSES = (
    "UNAVAILABLE",
    "UNKNOWN",
    "AVAILABLE",
    "LIMITED",
    "STANDING_PLUS_SEAT",
    "STANDING_ONLY",
    "NOT_ENOUGH_SEATS",
    "SOLD_OUT",
    "WAITLIST_AVAILABLE",
    "RESERVATION_COMPLETED",
    "NOT_OFFERED",
    "DEPARTED",
    "OUT_OF_SERVICE",
    "STALE",
    "ERROR",
)
_BROWSER_SNAPSHOT_STATUSES = (
    "AVAILABLE",
    "LIMITED",
    "STANDING_PLUS_SEAT",
    "STANDING_ONLY",
    "SOLD_OUT",
    "WAITLIST_AVAILABLE",
    "NOT_OFFERED",
)


def _allowed(column: str, values: tuple[str, ...]) -> str:
    choices = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({choices})"


def upgrade() -> None:
    with op.batch_alter_table("seat_observations") as batch_op:
        batch_op.drop_constraint(_OBSERVATION_CONSTRAINT, type_="check")
        batch_op.create_check_constraint(
            _OBSERVATION_CONSTRAINT,
            _allowed("status", _OBSERVATION_STATUSES),
        )
    with op.batch_alter_table("korail_browser_seat_snapshots") as batch_op:
        batch_op.drop_constraint(_BROWSER_SNAPSHOT_CONSTRAINT, type_="check")
        batch_op.create_check_constraint(
            _BROWSER_SNAPSHOT_CONSTRAINT,
            _allowed("status", _BROWSER_SNAPSHOT_STATUSES),
        )


def downgrade() -> None:
    op.execute("UPDATE seat_observations SET status = 'SOLD_OUT' WHERE status = 'STANDING_ONLY'")
    op.execute(
        "UPDATE korail_browser_seat_snapshots SET status = 'SOLD_OUT' "
        "WHERE status = 'STANDING_ONLY'"
    )
    with op.batch_alter_table("seat_observations") as batch_op:
        batch_op.drop_constraint(_OBSERVATION_CONSTRAINT, type_="check")
        batch_op.create_check_constraint(
            _OBSERVATION_CONSTRAINT,
            _allowed(
                "status",
                tuple(value for value in _OBSERVATION_STATUSES if value != "STANDING_ONLY"),
            ),
        )
    with op.batch_alter_table("korail_browser_seat_snapshots") as batch_op:
        batch_op.drop_constraint(_BROWSER_SNAPSHOT_CONSTRAINT, type_="check")
        batch_op.create_check_constraint(
            _BROWSER_SNAPSHOT_CONSTRAINT,
            _allowed(
                "status",
                tuple(value for value in _BROWSER_SNAPSHOT_STATUSES if value != "STANDING_ONLY"),
            ),
        )
