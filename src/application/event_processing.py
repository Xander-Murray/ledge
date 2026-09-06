from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from application.synchronization import (
    SynchronizationError,
    SyncResult,
    TransactionSynchronizer,
)
from domain.ledger import (
    TransactionConflictError,
    TransactionNotFoundError,
    TransactionStateError,
)
from persistence.models import InboundEventModel, TransactionSyncStateModel
from providers.base import ProviderError, TransactionProvider

TRANSACTION_UPDATE_EVENT_TYPE = "transactions.updated"
DEFAULT_PROCESSING_LEASE = timedelta(minutes=5)


class InboundEventNotFoundError(LookupError):
    """Raised when a worker receives an unknown inbox event ID."""


class InboundEventBusyError(RuntimeError):
    """Raised when another worker owns an event's unexpired lease."""


class InboundEventClaimLostError(RuntimeError):
    """Raised when a stale worker tries to update a newer worker's claim."""


class InboundEventStateError(RuntimeError):
    """Raised when an inbox event cannot enter processing from its current state."""


class ProviderNotConfiguredError(LookupError):
    """Raised when no local adapter is registered for an event's provider."""


class UnsupportedInboundEventTypeError(ValueError):
    """Raised when no processor exists for an accepted event type."""


@dataclass(frozen=True, slots=True)
class EventProcessingResult:
    """Measurable outcome of processing or safely skipping one inbox event."""

    event_id: UUID
    status: str
    already_processed: bool
    attempt_count: int
    processing_started_at: datetime
    processed_at: datetime
    processing_duration_ms: float
    synchronization: SyncResult | None


@dataclass(frozen=True, slots=True)
class _EventClaim:
    event_id: UUID
    token: UUID
    user_id: UUID
    provider_name: str
    provider_connection_id: str
    attempt_count: int
    processing_started_at: datetime


class InboundEventProcessor:
    """Claim and process one durable transaction notification."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        providers: Mapping[str, TransactionProvider],
        lease_timeout: timedelta = DEFAULT_PROCESSING_LEASE,
        clock: Callable[[], datetime] | None = None,
        token_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if lease_timeout <= timedelta(0):
            raise ValueError("lease_timeout must be positive")
        self._session_factory = session_factory
        self._providers = MappingProxyType(dict(providers))
        self._lease_timeout = lease_timeout
        self._clock = clock or (lambda: datetime.now(UTC))
        self._token_factory = token_factory

    def process(self, event_id: UUID) -> EventProcessingResult:
        """Process an event using short claim and completion transactions."""
        claim_or_result = self._claim(event_id)
        if isinstance(claim_or_result, EventProcessingResult):
            return claim_or_result
        claim = claim_or_result
        provider = self._providers[claim.provider_name]

        try:
            synchronization = TransactionSynchronizer(
                session_factory=self._session_factory,
                provider=provider,
            ).synchronize(
                user_id=claim.user_id,
                provider_name=claim.provider_name,
                provider_connection_id=claim.provider_connection_id,
            )
        except (
            ProviderError,
            SynchronizationError,
            TransactionConflictError,
            TransactionNotFoundError,
            TransactionStateError,
            SQLAlchemyError,
        ) as error:
            self._record_failure(claim, self._failure_code(error))
            raise

        return self._complete(claim, synchronization)

    def _claim(self, event_id: UUID) -> _EventClaim | EventProcessingResult:
        now = self._now()
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
                return self._processed_result(event)
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
            if sync_state.provider_name not in self._providers:
                raise ProviderNotConfiguredError(
                    f"Provider {sync_state.provider_name!r} is not configured"
                )

            if event.status == "processing":
                if event.processing_started_at is None:
                    raise RuntimeError(
                        f"Processing event {event.id} has no lease timestamp"
                    )
                if event.processing_started_at + self._lease_timeout > now:
                    raise InboundEventBusyError(
                        f"Inbound event {event.id} has an active processing lease"
                    )
            elif event.status not in {"pending", "failed"}:
                raise InboundEventStateError(
                    f"Inbound event {event.id} cannot process from status "
                    f"{event.status!r}"
                )

            token = self._token_factory()
            event.status = "processing"
            event.attempt_count += 1
            event.processing_token = token
            event.processing_started_at = now
            event.processed_at = None
            event.last_error_code = None
            session.flush()

            return _EventClaim(
                event_id=event.id,
                token=token,
                user_id=sync_state.user_id,
                provider_name=sync_state.provider_name,
                provider_connection_id=sync_state.provider_connection_id,
                attempt_count=event.attempt_count,
                processing_started_at=now,
            )

    def _complete(
        self,
        claim: _EventClaim,
        synchronization: SyncResult,
    ) -> EventProcessingResult:
        processed_at = self._now()
        with self._session_factory() as session, session.begin():
            event = self._load_claimed_event(session, claim)
            event.status = "processed"
            event.processing_token = None
            event.processed_at = processed_at
            event.last_error_code = None
            session.flush()

        return EventProcessingResult(
            event_id=claim.event_id,
            status="processed",
            already_processed=False,
            attempt_count=claim.attempt_count,
            processing_started_at=claim.processing_started_at,
            processed_at=processed_at,
            processing_duration_ms=self._duration_ms(
                claim.processing_started_at,
                processed_at,
            ),
            synchronization=synchronization,
        )

    def _record_failure(self, claim: _EventClaim, failure_code: str) -> None:
        failed_at = self._now()
        with self._session_factory() as session, session.begin():
            event = self._load_claimed_event(session, claim)
            event.status = "failed"
            event.processing_token = None
            event.processed_at = failed_at
            event.last_error_code = failure_code
            session.flush()

    @staticmethod
    def _load_claimed_event(
        session: Session,
        claim: _EventClaim,
    ) -> InboundEventModel:
        event = session.scalar(
            select(InboundEventModel)
            .where(InboundEventModel.id == claim.event_id)
            .with_for_update()
        )
        if event is None:
            raise InboundEventNotFoundError(
                f"Inbound event {claim.event_id} no longer exists"
            )
        if event.status != "processing" or event.processing_token != claim.token:
            raise InboundEventClaimLostError(
                f"Worker no longer owns inbound event {claim.event_id}"
            )
        return event

    def _processed_result(self, event: InboundEventModel) -> EventProcessingResult:
        if event.processing_started_at is None or event.processed_at is None:
            raise RuntimeError(
                f"Processed event {event.id} is missing processing timestamps"
            )
        return EventProcessingResult(
            event_id=event.id,
            status=event.status,
            already_processed=True,
            attempt_count=event.attempt_count,
            processing_started_at=event.processing_started_at,
            processed_at=event.processed_at,
            processing_duration_ms=self._duration_ms(
                event.processing_started_at,
                event.processed_at,
            ),
            synchronization=None,
        )

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return now

    @staticmethod
    def _duration_ms(started_at: datetime, ended_at: datetime) -> float:
        return max(0.0, (ended_at - started_at).total_seconds() * 1_000)

    @staticmethod
    def _failure_code(error: Exception) -> str:
        if isinstance(error, ProviderError):
            return "provider_error"
        if isinstance(error, SQLAlchemyError):
            return "database_error"
        if isinstance(error, SynchronizationError):
            return "synchronization_error"
        return "ledger_error"
