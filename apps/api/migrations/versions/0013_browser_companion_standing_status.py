"""Allow standing-plus-seat status in KORAIL browser companion snapshots."""

from alembic import op

revision = "0013_browser_standing_status"
down_revision = "0012_browser_companion_pairing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("korail_browser_seat_snapshots") as batch_op:
        batch_op.drop_constraint("ck_korail_browser_snapshot_status", type_="check")
        batch_op.create_check_constraint(
            "ck_korail_browser_snapshot_status",
            "status IN ('AVAILABLE', 'LIMITED', 'STANDING_PLUS_SEAT', 'SOLD_OUT', "
            "'WAITLIST_AVAILABLE', 'NOT_OFFERED')",
        )


def downgrade() -> None:
    op.execute(
        "UPDATE korail_browser_seat_snapshots SET status = 'AVAILABLE' "
        "WHERE status = 'STANDING_PLUS_SEAT'"
    )
    with op.batch_alter_table("korail_browser_seat_snapshots") as batch_op:
        batch_op.drop_constraint("ck_korail_browser_snapshot_status", type_="check")
        batch_op.create_check_constraint(
            "ck_korail_browser_snapshot_status",
            "status IN ('AVAILABLE', 'LIMITED', 'SOLD_OUT', 'WAITLIST_AVAILABLE', 'NOT_OFFERED')",
        )
