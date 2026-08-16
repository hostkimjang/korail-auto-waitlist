"""Fence legacy external provider failures whose dispatch status was not persisted."""

import sqlalchemy as sa
from alembic import op

revision = "0040_legacy_failed_unknown"
down_revision = "0039_confirmation_corr_seats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE reservation_attempts "
            "SET outcome = 'UNKNOWN', "
            "next_reconcile_at = COALESCE(next_reconcile_at, CURRENT_TIMESTAMP) "
            "WHERE outcome = 'FAILED' "
            "AND result_reason_code = 'PROVIDER_UNAVAILABLE' "
            "AND finished_at IS NOT NULL "
            "AND credential_version IS NOT NULL "
            "AND confirmation_outcome IS NULL "
            "AND confirmation_source IS NULL "
            "AND confirmation_observed_at IS NULL "
            "AND reconciliation_attempt_count = 0 "
            "AND reconciliation_resolution IS NULL "
            "AND candidate_id IN ("
            "SELECT candidates.id FROM watch_candidates AS candidates "
            "JOIN watches AS watches ON watches.id = candidates.watch_id "
            "WHERE watches.provider IN ('KORAIL', 'SRT')"
            ")"
        )
    )


def downgrade() -> None:
    # Restoring FAILED would remove the duplicate-booking fence from commands that
    # an older adapter may already have dispatched. This data normalization is
    # intentionally irreversible; restoring a pre-upgrade backup is the safe rollback.
    pass
