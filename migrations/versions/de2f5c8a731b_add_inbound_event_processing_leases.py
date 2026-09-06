"""add inbound event processing leases

Revision ID: de2f5c8a731b
Revises: 77ae70e564d7
Create Date: 2026-09-06 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "de2f5c8a731b"
down_revision: str | Sequence[str] | None = "77ae70e564d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add retry, timing, and exclusive-claim state to inbound events."""
    op.add_column(
        "inbound_events",
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "inbound_events",
        sa.Column("processing_token", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "inbound_events",
        sa.Column(
            "processing_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "inbound_events",
        sa.Column("last_error_code", sa.String(), nullable=True),
    )

    # The previous worker never committed its temporary processing state. Reset
    # any manually-created processing rows so the new lease model can claim them.
    op.execute(
        sa.text(
            "UPDATE inbound_events SET status = 'pending', processed_at = NULL "
            "WHERE status = 'processing'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE inbound_events "
            "SET attempt_count = 1, processing_started_at = received_at "
            "WHERE status IN ('processed', 'failed')"
        )
    )
    op.execute(
        sa.text(
            "UPDATE inbound_events SET last_error_code = 'legacy_failure' "
            "WHERE status = 'failed'"
        )
    )

    op.drop_constraint(
        op.f("ck_inbound_events_processing_timestamp"),
        "inbound_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_inbound_events_attempt_count"),
        "inbound_events",
        "(status = 'pending' AND attempt_count = 0) OR "
        "(status <> 'pending' AND attempt_count >= 1)",
    )
    op.create_check_constraint(
        op.f("ck_inbound_events_processing_timestamp"),
        "inbound_events",
        "(status = 'pending' AND processing_token IS NULL AND "
        "processing_started_at IS NULL AND processed_at IS NULL) OR "
        "(status = 'processing' AND processing_token IS NOT NULL AND "
        "processing_started_at IS NOT NULL AND processed_at IS NULL) OR "
        "(status IN ('processed', 'failed') AND processing_token IS NULL AND "
        "processing_started_at IS NOT NULL AND processed_at IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_inbound_events_failure_code"),
        "inbound_events",
        "(status = 'failed' AND length(trim(last_error_code)) > 0) OR "
        "(status <> 'failed' AND last_error_code IS NULL)",
    )
    op.create_index(
        "ix_inbound_events_status_processing_started_at",
        "inbound_events",
        ["status", "processing_started_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove processing leases while retaining the original event states."""
    op.drop_index(
        "ix_inbound_events_status_processing_started_at",
        table_name="inbound_events",
    )
    op.drop_constraint(
        op.f("ck_inbound_events_failure_code"),
        "inbound_events",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_inbound_events_processing_timestamp"),
        "inbound_events",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_inbound_events_attempt_count"),
        "inbound_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_inbound_events_processing_timestamp"),
        "inbound_events",
        "(status IN ('pending', 'processing') AND processed_at IS NULL) OR "
        "(status IN ('processed', 'failed') AND processed_at IS NOT NULL)",
    )
    op.drop_column("inbound_events", "last_error_code")
    op.drop_column("inbound_events", "processing_started_at")
    op.drop_column("inbound_events", "processing_token")
    op.drop_column("inbound_events", "attempt_count")
