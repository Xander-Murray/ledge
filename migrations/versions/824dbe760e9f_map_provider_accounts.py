"""map provider accounts

Revision ID: 824dbe760e9f
Revises: de2f5c8a731b
Create Date: 2026-09-07 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "824dbe760e9f"
down_revision: str | Sequence[str] | None = "de2f5c8a731b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store ownership-safe mappings from provider accounts to Ledge accounts."""
    op.create_unique_constraint(
        "uq_transaction_sync_states_ownership",
        "transaction_sync_states",
        ["id", "user_id"],
    )
    op.create_table(
        "provider_account_mappings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_sync_state_id", sa.Uuid(), nullable=False),
        sa.Column("financial_account_id", sa.Uuid(), nullable=False),
        sa.Column("provider_account_id", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(trim(provider_account_id)) > 0",
            name=op.f("ck_provider_account_mappings_provider_account_id_nonempty"),
        ),
        sa.ForeignKeyConstraint(
            ["financial_account_id", "user_id"],
            ["financial_accounts.id", "financial_accounts.user_id"],
            name=op.f(
                "fk_provider_account_mappings_financial_account_id_financial_accounts"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_sync_state_id", "user_id"],
            ["transaction_sync_states.id", "transaction_sync_states.user_id"],
            name=op.f(
                "fk_provider_account_mappings_transaction_sync_state_id_"
                "transaction_sync_states"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_account_mappings")),
        sa.UniqueConstraint(
            "financial_account_id",
            name="uq_provider_account_mappings_financial_account",
        ),
        sa.UniqueConstraint(
            "transaction_sync_state_id",
            "provider_account_id",
            name="uq_provider_account_mappings_provider_account",
        ),
    )
    op.create_index(
        op.f("ix_provider_account_mappings_transaction_sync_state_id"),
        "provider_account_mappings",
        ["transaction_sync_state_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove provider account mappings."""
    op.drop_index(
        op.f("ix_provider_account_mappings_transaction_sync_state_id"),
        table_name="provider_account_mappings",
    )
    op.drop_table("provider_account_mappings")
    op.drop_constraint(
        "uq_transaction_sync_states_ownership",
        "transaction_sync_states",
        type_="unique",
    )
