"""support pending transaction replacement

Revision ID: 338159bae2ac
Revises: 55489047a1ab
Create Date: 2026-09-02 09:02:18.888988

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "338159bae2ac"
down_revision: str | Sequence[str] | None = "55489047a1ab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "external_transactions",
        sa.Column(
            "is_pending",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )
    op.add_column(
        "external_transactions",
        sa.Column("pending_provider_transaction_id", sa.String(), nullable=True),
    )
    op.drop_constraint(
        op.f("ck_external_transactions_status"),
        "external_transactions",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_external_transactions_status"),
        "external_transactions",
        "status IN ('active', 'removed', 'replaced')",
    )
    op.create_unique_constraint(
        "uq_external_transactions_pending_replacement",
        "external_transactions",
        ["user_id", "pending_provider_transaction_id"],
    )
    op.create_foreign_key(
        op.f("fk_external_transactions_user_id_external_transactions"),
        "external_transactions",
        "external_transactions",
        ["user_id", "pending_provider_transaction_id"],
        ["user_id", "provider_transaction_id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_external_transactions_not_self_pending_replacement"),
        "external_transactions",
        "pending_provider_transaction_id IS NULL "
        "OR pending_provider_transaction_id <> provider_transaction_id",
    )
    op.create_check_constraint(
        op.f("ck_external_transactions_only_pending_can_be_replaced"),
        "external_transactions",
        "status <> 'replaced' OR is_pending",
    )
    op.create_check_constraint(
        op.f("ck_external_transactions_pending_has_no_replacement_source"),
        "external_transactions",
        "NOT is_pending OR pending_provider_transaction_id IS NULL",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f("ck_external_transactions_pending_has_no_replacement_source"),
        "external_transactions",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_external_transactions_only_pending_can_be_replaced"),
        "external_transactions",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_external_transactions_not_self_pending_replacement"),
        "external_transactions",
        type_="check",
    )
    op.drop_constraint(
        op.f("fk_external_transactions_user_id_external_transactions"),
        "external_transactions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_external_transactions_pending_replacement",
        "external_transactions",
        type_="unique",
    )
    op.execute(
        sa.text(
            "UPDATE external_transactions SET status = 'removed' "
            "WHERE status = 'replaced'"
        )
    )
    op.drop_constraint(
        op.f("ck_external_transactions_status"),
        "external_transactions",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_external_transactions_status"),
        "external_transactions",
        "status IN ('active', 'removed')",
    )
    op.drop_column("external_transactions", "pending_provider_transaction_id")
    op.drop_column("external_transactions", "is_pending")
