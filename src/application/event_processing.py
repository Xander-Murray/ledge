from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from application.synchronization import SyncResult, TransactionSynchronizer
from persistence.models import InboundEventModel, TransactionSyncStateModel
from providers.base import TransactionProvider

TRANSACTION_UPDATE_EVENT_TYPE = "transactions.updated"


class InboundEventNotFoundError(LookupError):
    """Raised when a worker receives an unknown inbox event ID."""


class InboundEventStateError(RuntimeError):
    """Raised when an inbox event cannot enter processing from its current state."""


class ProviderNotConfiguredError(LookupError):
    """Raised when no local adapter is registered for an event's provider."""


class UnsupportedInboundEventTypeError(ValueError):
    """Raised when no processor exists for an accepted event type."""


@dataclass(frozen=True, slots=True)
class EventProcessingResult:
    """Outcome of processing or safely skipping one inbox event."""

    event_id: UUID
    already_processed: bool
    synchronization: SyncResult | None


class InboundEventProcessor:
    """Turn one durable transaction notification into one synchronization run."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        providers: Mapping[str, TransactionProvider],
    ) -> None:
        self._session_factory = session_factory
        self._providers = MappingProxyType(dict(providers))

    def process(self, event_id: UUID) -> EventProcessingResult:
        """Process one event while serializing duplicate workers on its row.

        The event row lock remains open around synchronization. If synchronization
        fails, the temporary ``processing`` state rolls back to its prior retryable
        state. The synchronizer owns a separate atomic ledger transaction.
        """
        with self._session_factory() as session, session.begin():
            event = session.scalar(
                select(InboundEventModel)
                .where(InboundEventModel.id == event_id)
                .with_for_update()
            )
            if event is None:
                raise InboundEventNotFoundError(
                    f"Inbound event {event_id} does not exist"
                )
            if event.status == "processed":
                return EventProcessingResult(
                    event_id=event.id,
                    already_processed=True,
                    synchronization=None,
                )
            if event.status == "processing":
                raise InboundEventStateError(
                    f"Inbound event {event.id} is already processing"
                )
            if event.status not in {"pending", "failed"}:
                raise InboundEventStateError(
                    f"Inbound event {event.id} cannot process from status "
                    f"{event.status!r}"
                )
            if event.event_type != TRANSACTION_UPDATE_EVENT_TYPE:
                raise UnsupportedInboundEventTypeError(
                    f"Inbound event type {event.event_type!r} is not supported"
                )

            sync_state = session.get(
                TransactionSyncStateModel,
                event.transaction_sync_state_id,
            )
            if sync_state is None:
                raise RuntimeError(
                    f"Inbound event {event.id} references a missing sync state"
                )

            provider = self._providers.get(sync_state.provider_name)
            if provider is None:
                raise ProviderNotConfiguredError(
                    f"Provider {sync_state.provider_name!r} is not configured"
                )

            event.status = "processing"
            event.processed_at = None
            session.flush()

            synchronization = TransactionSynchronizer(
                session_factory=self._session_factory,
                provider=provider,
            ).synchronize(
                user_id=sync_state.user_id,
                provider_name=sync_state.provider_name,
                provider_connection_id=sync_state.provider_connection_id,
            )

            event.status = "processed"
            event.processed_at = datetime.now(UTC)
            session.flush()

        return EventProcessingResult(
            event_id=event_id,
            already_processed=False,
            synchronization=synchronization,
        )
