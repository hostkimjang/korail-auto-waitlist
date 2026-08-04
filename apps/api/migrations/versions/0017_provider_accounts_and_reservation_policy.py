"""Store encrypted rail accounts and an explicit per-watch reservation policy."""

import sqlalchemy as sa
from alembic import op

revision = "0017_provider_accounts_policy"
down_revision = "0016_admin_ui_preferences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    reservation_policy = sa.Enum(
        "NOTIFY_ONLY",
        "RESERVE_ONCE_BEFORE_PAYMENT",
        name="reservationpolicy",
        native_enum=False,
    )
    with op.batch_alter_table("watches") as batch_op:
        batch_op.add_column(
            sa.Column(
                "reservation_policy",
                reservation_policy,
                nullable=False,
                server_default="NOTIFY_ONLY",
            )
        )
        batch_op.create_check_constraint(
            "ck_watch_reservation_policy_allowed",
            "reservation_policy IN ('NOTIFY_ONLY', 'RESERVE_ONCE_BEFORE_PAYMENT')",
        )

    op.create_table(
        "rail_provider_accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider", sa.String(6), nullable=False),
        sa.Column("credentials_ciphertext", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("credential_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "last_auth_status",
            sa.String(32),
            nullable=False,
            server_default="not_checked",
        ),
        sa.Column("last_authenticated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "provider IN ('KORAIL', 'SRT')",
            name="ck_rail_provider_account_provider_allowed",
        ),
        sa.CheckConstraint(
            "length(trim(credentials_ciphertext)) > 0",
            name="ck_rail_provider_account_ciphertext_nonempty",
        ),
        sa.CheckConstraint(
            "credential_version >= 1",
            name="ck_rail_provider_account_version_positive",
        ),
        sa.CheckConstraint(
            "last_auth_status IN ('not_checked', 'authenticated', 'auth_required', "
            "'provider_blocked', 'failed')",
            name="ck_rail_provider_account_auth_status_allowed",
        ),
        sa.UniqueConstraint("provider", name="uq_rail_provider_accounts_provider"),
    )
    op.create_index(
        "ix_rail_provider_accounts_provider",
        "rail_provider_accounts",
        ["provider"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("rail_provider_accounts")
    with op.batch_alter_table("watches") as batch_op:
        batch_op.drop_constraint(
            "ck_watch_reservation_policy_allowed",
            type_="check",
        )
        batch_op.drop_column("reservation_policy")
