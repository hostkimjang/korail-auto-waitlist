"""Allow safe reservation retries across distinct availability/auth episodes."""

import sqlalchemy as sa
from alembic import op

revision = "0018_reservation_episodes"
down_revision = "0017_provider_accounts_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("reservation_attempts") as batch_op:
        batch_op.drop_constraint(
            "uq_reservation_attempt_candidate_id",
            type_="unique",
        )
        batch_op.add_column(
            sa.Column(
                "attempt_sequence",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )
        batch_op.add_column(
            sa.Column(
                "episode_key",
                sa.String(128),
                nullable=False,
                server_default="legacy",
            )
        )

    op.execute(
        "UPDATE reservation_attempts "
        "SET episode_key = 'legacy:' || candidate_id"
    )

    with op.batch_alter_table("reservation_attempts") as batch_op:
        batch_op.alter_column("episode_key", server_default=None)
        batch_op.create_check_constraint(
            "ck_reservation_attempt_episode_key_nonempty",
            "length(trim(episode_key)) > 0",
        )
        batch_op.create_check_constraint(
            "ck_reservation_attempt_sequence_positive",
            "attempt_sequence >= 1",
        )
        batch_op.create_unique_constraint(
            "uq_reservation_attempt_candidate_sequence",
            ["candidate_id", "attempt_sequence"],
        )
        batch_op.create_unique_constraint(
            "uq_reservation_attempt_candidate_episode",
            ["candidate_id", "episode_key"],
        )


def downgrade() -> None:
    # Older code can represent only one attempt per candidate. Preserve the earliest
    # audit row and discard later sequence rows before restoring that contract.
    op.execute("DELETE FROM reservation_attempts WHERE attempt_sequence > 1")
    with op.batch_alter_table("reservation_attempts") as batch_op:
        batch_op.drop_constraint(
            "uq_reservation_attempt_candidate_episode",
            type_="unique",
        )
        batch_op.drop_constraint(
            "uq_reservation_attempt_candidate_sequence",
            type_="unique",
        )
        batch_op.drop_constraint(
            "ck_reservation_attempt_sequence_positive",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_reservation_attempt_episode_key_nonempty",
            type_="check",
        )
        batch_op.drop_column("episode_key")
        batch_op.drop_column("attempt_sequence")
        batch_op.create_unique_constraint(
            "uq_reservation_attempt_candidate_id",
            ["candidate_id"],
        )
