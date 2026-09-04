from __future__ import annotations

from sqlalchemy import CheckConstraint, DateTime, Index, UniqueConstraint, inspect
from sqlalchemy.dialects.postgresql import JSONB

from persistence.models import InboundEventModel, TransactionSyncStateModel


def test_inbound_event_has_durable_payload_and_processing_fields() -> None:
    columns = InboundEventModel.__table__.c

    assert set(columns.keys()) == {
        "id",
        "transaction_sync_state_id",
        "provider_event_id",
        "event_type",
        "raw_payload",
        "status",
        "received_at",
        "processed_at",
    }
    assert isinstance(columns.raw_payload.type, JSONB)
    assert columns.raw_payload.nullable is False
    assert columns.status.server_default is not None
    assert isinstance(columns.received_at.type, DateTime)
    assert columns.received_at.type.timezone is True
    assert columns.received_at.server_default is not None
    assert columns.processed_at.nullable is True


def test_inbound_event_enforces_identity_and_lifecycle_constraints() -> None:
    table = InboundEventModel.__table__
    foreign_keys = tuple(table.c.transaction_sync_state_id.foreign_keys)
    unique_constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in table.indexes
        if isinstance(index, Index)
    }

    assert len(foreign_keys) == 1
    assert foreign_keys[0].target_fullname == "transaction_sync_states.id"
    assert foreign_keys[0].ondelete == "RESTRICT"
    assert unique_constraints["uq_inbound_events_sync_state_provider_event"] == (
        "transaction_sync_state_id",
        "provider_event_id",
    )
    assert check_names == {
        "ck_inbound_events_event_type_nonempty",
        "ck_inbound_events_processing_timestamp",
        "ck_inbound_events_provider_event_id_nonempty",
        "ck_inbound_events_status",
    }
    assert indexes["ix_inbound_events_status_received_at"] == (
        "status",
        "received_at",
    )


def test_sync_state_and_inbound_event_relationships_are_bidirectional() -> None:
    sync_state_events = inspect(TransactionSyncStateModel).relationships[
        "inbound_events"
    ]
    event_sync_state = inspect(InboundEventModel).relationships["sync_state"]

    assert sync_state_events.mapper.class_ is InboundEventModel
    assert sync_state_events.back_populates == "sync_state"
    assert sync_state_events.uselist is True
    assert event_sync_state.mapper.class_ is TransactionSyncStateModel
    assert event_sync_state.back_populates == "inbound_events"
    assert event_sync_state.uselist is False
