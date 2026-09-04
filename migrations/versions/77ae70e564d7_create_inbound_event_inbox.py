"""create inbound event inbox

Revision ID: 77ae70e564d7
Revises: 338159bae2ac
Create Date: 2026-09-04 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "77ae70e564d7"
down_revision: str | Sequence[str] | None = "338159bae2ac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the durable inbox used to accept provider notifications."""
    op.create_table(
        "inbound_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("transaction_sync_state_id", sa.Uuid(), nullable=False),
        sa.Column("provider_event_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column(
            "raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "status",
            sa.String(),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "length(trim(event_type)) > 0",
            name=op.f("ck_inbound_events_event_type_nonempty"),
        ),
        sa.CheckConstraint(
            "length(trim(provider_event_id)) > 0",
            name=op.f("ck_inbound_events_provider_event_id_nonempty"),
        ),
        sa.CheckConstraint(
            "(status IN ('pending', 'processing') AND processed_at IS NULL) OR "
            "(status IN ('processed', 'failed') AND processed_at IS NOT NULL)",
            name=op.f("ck_inbound_events_processing_timestamp"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'processed', 'failed')",
            name=op.f("ck_inbound_events_status"),
        ),
        sa.ForeignKeyConstraint(
            ["transaction_sync_state_id"],
            ["transaction_sync_states.id"],
            name=op.f(
                "fk_inbound_events_transaction_sync_state_id_transaction_sync_states"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inbound_events")),
        sa.UniqueConstraint(
            "transaction_sync_state_id",
            "provider_event_id",
            name="uq_inbound_events_sync_state_provider_event",
        ),
    )
    op.create_index(
        "ix_inbound_events_status_received_at",
        "inbound_events",
        ["status", "received_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove the provider-notification inbox."""
    op.drop_index(
        "ix_inbound_events_status_received_at",
        table_name="inbound_events",
    )
    op.drop_table("inbound_events")
