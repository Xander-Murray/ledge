from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from persistence.models import InboundEventModel, TransactionSyncStateModel


class ProviderConnectionNotFoundError(LookupError):
    """Raised when an event targets no connection owned by the user."""


class InboundEventConflictError(RuntimeError):
    """Raised when a provider event identity is reused for different data."""


async def accept_inbound_event(
    session: AsyncSession,
    *,
    user_id: UUID,
    provider_name: str,
    provider_connection_id: str,
    provider_event_id: str,
    event_type: str,
    raw_payload: dict[str, Any],
) -> InboundEventModel:
    """Stage one idempotent event inside the caller-owned transaction."""
    sync_state_id = await session.scalar(
        select(TransactionSyncStateModel.id).where(
            TransactionSyncStateModel.user_id == user_id,
            TransactionSyncStateModel.provider_name == provider_name,
            TransactionSyncStateModel.provider_connection_id == provider_connection_id,
        )
    )
    if sync_state_id is None:
        raise ProviderConnectionNotFoundError(
            f"Provider connection {provider_name!r}/{provider_connection_id!r} "
            "does not exist"
        )

    statement = (
        insert(InboundEventModel)
        .values(
            id=uuid4(),
            transaction_sync_state_id=sync_state_id,
            provider_event_id=provider_event_id,
            event_type=event_type,
            raw_payload=raw_payload,
            status="pending",
        )
        .on_conflict_do_nothing(
            constraint="uq_inbound_events_sync_state_provider_event"
        )
        .returning(InboundEventModel)
    )
    accepted_event = await session.scalar(statement)
    if accepted_event is not None:
        return accepted_event

    existing_event = await session.scalar(
        select(InboundEventModel).where(
            InboundEventModel.transaction_sync_state_id == sync_state_id,
            InboundEventModel.provider_event_id == provider_event_id,
        )
    )
    if existing_event is None:
        raise RuntimeError("Inbound event conflict did not return an existing row")
    if (
        existing_event.event_type != event_type
        or existing_event.raw_payload != raw_payload
    ):
        raise InboundEventConflictError(
            f"Provider event {provider_event_id!r} already exists with different data"
        )

    return existing_event
