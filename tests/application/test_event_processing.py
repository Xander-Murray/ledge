from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Lock
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from application.event_processing import (
    InboundEventBusyError,
    InboundEventClaimLostError,
    InboundEventNotFoundError,
    InboundEventProcessor,
    ProviderNotConfiguredError,
    UnsupportedInboundEventTypeError,
)
from domain.ledger import TransactionNotFoundError
from domain.models import Transaction
from persistence.database import create_session_factory
from persistence.models import (
    ExternalTransactionModel,
    FinancialAccountModel,
    InboundEventModel,
    TransactionSyncStateModel,
    UserModel,
)
from providers.base import TransactionProvider, TransactionSyncPage
from providers.fake import FakeTransactionProvider, UnknownProviderCursorError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ACCOUNT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
PROVIDER_NAME = "fake"
PROVIDER_CONNECTION_ID = "connection-1"


@dataclass(frozen=True)
class EventProcessingDatabase:
    engine: Engine
    session_factory: sessionmaker[Session]
    user_id: UUID
    sync_state_id: UUID
    event_id: UUID


class CoordinatedProvider:
    """Pause a fetch so another worker can inspect or reclaim its lease."""

    def __init__(self) -> None:
        self.fetch_started = Event()
        self.release_fetch = Event()
        self._call_lock = Lock()
        self.call_count = 0

    def fetch_transaction_updates(self, cursor: str | None) -> TransactionSyncPage:
        with self._call_lock:
            self.call_count += 1
        self.fetch_started.set()
        if not self.release_fetch.wait(timeout=5):
            raise TimeoutError("test did not release provider fetch")
        return TransactionSyncPage(
            added=(),
            modified=(),
            removed=(),
            next_cursor="cursor-1",
            has_more=False,
        )


@dataclass
class MutableClock:
    current: datetime

    def __call__(self) -> datetime:
        return self.current

    def advance(self, elapsed: timedelta) -> None:
        self.current += elapsed


class AdvancingProvider:
    """Advance a test clock while simulating provider request latency."""

    def __init__(
        self,
        provider: TransactionProvider,
        clock: MutableClock,
        elapsed: timedelta,
    ) -> None:
        self._provider = provider
        self._clock = clock
        self._elapsed = elapsed

    def fetch_transaction_updates(self, cursor: str | None) -> TransactionSyncPage:
        page = self._provider.fetch_transaction_updates(cursor)
        self._clock.advance(self._elapsed)
        return page


def _get_test_database_url() -> str:
    database_url = os.environ.get("LEDGE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("Set LEDGE_TEST_DATABASE_URL to run event-processing tests")

    database_name = make_url(database_url).database
    if database_name is None or not database_name.endswith("_test"):
        pytest.fail("Event-processing tests require a database ending in '_test'")

    return database_url


@pytest.fixture
def event_processing_database(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[EventProcessingDatabase]:
    database_url = _get_test_database_url()
    monkeypatch.setenv("LEDGE_DATABASE_URL", database_url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))

    command.downgrade(config, "base")
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)
    user_id = uuid4()
    sync_state_id = uuid4()
    event_id = uuid4()
    with session_factory() as session, session.begin():
        session.add(UserModel(id=user_id))
        session.add(
            FinancialAccountModel(
                id=ACCOUNT_ID,
                user_id=user_id,
                name="Checking",
                account_type="checking",
            )
        )
        session.add(
            TransactionSyncStateModel(
                id=sync_state_id,
                user_id=user_id,
                provider_name=PROVIDER_NAME,
                provider_connection_id=PROVIDER_CONNECTION_ID,
                cursor=None,
            )
        )
        session.add(
            InboundEventModel(
                id=event_id,
                transaction_sync_state_id=sync_state_id,
                provider_event_id="event-1",
                event_type="transactions.updated",
                raw_payload={"webhook_code": "SYNC_UPDATES_AVAILABLE"},
                status="pending",
            )
        )

    try:
        yield EventProcessingDatabase(
            engine=engine,
            session_factory=session_factory,
            user_id=user_id,
            sync_state_id=sync_state_id,
            event_id=event_id,
        )
    finally:
        engine.dispose()
        command.downgrade(config, "base")


def _processor(
    database: EventProcessingDatabase,
    provider: TransactionProvider,
    *,
    lease_timeout: timedelta = timedelta(minutes=5),
    clock: Callable[[], datetime] | None = None,
) -> InboundEventProcessor:
    return InboundEventProcessor(
        session_factory=database.session_factory,
        providers={PROVIDER_NAME: provider},
        lease_timeout=lease_timeout,
        clock=clock,
    )


def _provider_with_one_transaction() -> FakeTransactionProvider:
    transaction = Transaction(
        account_id=ACCOUNT_ID,
        provider_transaction_id="transaction-1",
        amount_cents=1_250,
        description="Neighborhood Market",
    )
    return FakeTransactionProvider(
        {
            None: TransactionSyncPage(
                added=(transaction,),
                modified=(),
                removed=(),
                next_cursor="cursor-1",
                has_more=False,
            ),
            "cursor-1": TransactionSyncPage(
                added=(),
                modified=(),
                removed=(),
                next_cursor="cursor-1",
                has_more=False,
            ),
        }
    )


@pytest.mark.integration
def test_pending_event_runs_synchronization_and_becomes_processed(
    event_processing_database: EventProcessingDatabase,
) -> None:
    clock = MutableClock(datetime(2026, 9, 6, 14, 0, tzinfo=UTC))
    inner_provider = _provider_with_one_transaction()
    provider = AdvancingProvider(
        inner_provider,
        clock,
        timedelta(milliseconds=275),
    )

    result = _processor(event_processing_database, provider, clock=clock).process(
        event_processing_database.event_id
    )

    assert result.event_id == event_processing_database.event_id
    assert result.status == "processed"
    assert result.already_processed is False
    assert result.attempt_count == 1
    assert result.processing_started_at == datetime(2026, 9, 6, 14, 0, tzinfo=UTC)
    assert result.processed_at == datetime(
        2026,
        9,
        6,
        14,
        0,
        0,
        275_000,
        tzinfo=UTC,
    )
    assert result.processing_duration_ms == 275
    assert result.synchronization is not None
    assert result.synchronization.ending_cursor == "cursor-1"
    assert result.synchronization.added_count == 1
    assert inner_provider.requested_cursors == [None]

    with event_processing_database.session_factory() as session:
        event = session.get(InboundEventModel, event_processing_database.event_id)
        sync_state = session.get(
            TransactionSyncStateModel,
            event_processing_database.sync_state_id,
        )

        assert event is not None
        assert event.status == "processed"
        assert event.attempt_count == 1
        assert event.processing_token is None
        assert event.processing_started_at == result.processing_started_at
        assert event.processed_at is not None
        assert event.last_error_code is None
        assert sync_state is not None
        assert sync_state.cursor == "cursor-1"
        assert session.scalar(select(func.count(ExternalTransactionModel.id))) == 1


@pytest.mark.integration
def test_processed_event_is_an_idempotent_no_op(
    event_processing_database: EventProcessingDatabase,
) -> None:
    provider = _provider_with_one_transaction()
    processor = _processor(event_processing_database, provider)
    processor.process(event_processing_database.event_id)

    result = processor.process(event_processing_database.event_id)

    assert result.already_processed is True
    assert result.status == "processed"
    assert result.attempt_count == 1
    assert result.synchronization is None
    assert provider.requested_cursors == [None]


@pytest.mark.integration
def test_failed_synchronization_records_failure_and_can_be_retried(
    event_processing_database: EventProcessingDatabase,
) -> None:
    missing_modification = Transaction(
        account_id=ACCOUNT_ID,
        provider_transaction_id="missing-transaction",
        amount_cents=1_400,
        description="Unknown transaction",
    )
    failing_provider = FakeTransactionProvider(
        {
            None: TransactionSyncPage(
                added=(),
                modified=(missing_modification,),
                removed=(),
                next_cursor="failed-cursor",
                has_more=False,
            )
        }
    )

    with pytest.raises(TransactionNotFoundError, match="does not exist"):
        _processor(event_processing_database, failing_provider).process(
            event_processing_database.event_id
        )

    with event_processing_database.session_factory() as session:
        event = session.get(InboundEventModel, event_processing_database.event_id)
        sync_state = session.get(
            TransactionSyncStateModel,
            event_processing_database.sync_state_id,
        )
        assert event is not None
        assert event.status == "failed"
        assert event.attempt_count == 1
        assert event.processing_token is None
        assert event.processing_started_at is not None
        assert event.processed_at is not None
        assert event.last_error_code == "ledger_error"
        assert sync_state is not None
        assert sync_state.cursor is None
        assert session.scalar(select(func.count(ExternalTransactionModel.id))) == 0

    retry_result = _processor(
        event_processing_database,
        _provider_with_one_transaction(),
    ).process(event_processing_database.event_id)

    assert retry_result.already_processed is False
    assert retry_result.status == "processed"
    assert retry_result.attempt_count == 2
    assert retry_result.synchronization is not None
    assert retry_result.synchronization.ending_cursor == "cursor-1"

    with event_processing_database.session_factory() as session:
        event = session.get(InboundEventModel, event_processing_database.event_id)
        assert event is not None
        assert event.status == "processed"
        assert event.attempt_count == 2
        assert event.processing_token is None
        assert event.last_error_code is None


@pytest.mark.integration
def test_duplicate_worker_is_rejected_while_the_first_lease_is_active(
    event_processing_database: EventProcessingDatabase,
) -> None:
    provider = CoordinatedProvider()
    processor = _processor(event_processing_database, provider)

    with ThreadPoolExecutor(max_workers=1) as executor:
        first_worker = executor.submit(
            processor.process,
            event_processing_database.event_id,
        )
        assert provider.fetch_started.wait(timeout=5)
        try:
            with pytest.raises(InboundEventBusyError, match="active processing lease"):
                processor.process(event_processing_database.event_id)
        finally:
            provider.release_fetch.set()
        result = first_worker.result(timeout=5)

    assert provider.call_count == 1
    assert result.already_processed is False


@pytest.mark.integration
def test_expired_processing_lease_is_reclaimed(
    event_processing_database: EventProcessingDatabase,
) -> None:
    now = datetime(2026, 9, 6, 15, 0, tzinfo=UTC)
    with event_processing_database.session_factory() as session, session.begin():
        event = session.get(InboundEventModel, event_processing_database.event_id)
        assert event is not None
        event.status = "processing"
        event.attempt_count = 1
        event.processing_token = uuid4()
        event.processing_started_at = now - timedelta(minutes=6)

    result = _processor(
        event_processing_database,
        _provider_with_one_transaction(),
        clock=lambda: now,
    ).process(event_processing_database.event_id)

    assert result.status == "processed"
    assert result.already_processed is False
    assert result.attempt_count == 2
    assert result.processing_started_at == now


@pytest.mark.integration
def test_expired_worker_cannot_overwrite_the_reclaiming_workers_result(
    event_processing_database: EventProcessingDatabase,
) -> None:
    clock = MutableClock(datetime(2026, 9, 6, 16, 0, tzinfo=UTC))
    stale_provider = CoordinatedProvider()
    stale_processor = _processor(
        event_processing_database,
        stale_provider,
        clock=clock,
    )
    reclaiming_provider = FakeTransactionProvider(
        {
            None: TransactionSyncPage(
                added=(),
                modified=(),
                removed=(),
                next_cursor="cursor-2",
                has_more=False,
            )
        }
    )
    reclaiming_processor = _processor(
        event_processing_database,
        reclaiming_provider,
        clock=clock,
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        stale_worker = executor.submit(
            stale_processor.process,
            event_processing_database.event_id,
        )
        assert stale_provider.fetch_started.wait(timeout=5)
        clock.advance(timedelta(minutes=6))
        try:
            reclaiming_result = reclaiming_processor.process(
                event_processing_database.event_id
            )
        finally:
            stale_provider.release_fetch.set()
        with pytest.raises(InboundEventClaimLostError, match="no longer owns"):
            stale_worker.result(timeout=5)

    assert reclaiming_result.status == "processed"
    assert reclaiming_result.attempt_count == 2
    with event_processing_database.session_factory() as session:
        event = session.get(InboundEventModel, event_processing_database.event_id)
        sync_state = session.get(
            TransactionSyncStateModel,
            event_processing_database.sync_state_id,
        )
        assert event is not None
        assert event.status == "processed"
        assert event.attempt_count == 2
        assert event.processing_token is None
        assert sync_state is not None
        assert sync_state.cursor == "cursor-2"


@pytest.mark.integration
def test_provider_failure_is_recorded_without_storing_the_error_message(
    event_processing_database: EventProcessingDatabase,
) -> None:
    with event_processing_database.session_factory() as session, session.begin():
        sync_state = session.get(
            TransactionSyncStateModel,
            event_processing_database.sync_state_id,
        )
        assert sync_state is not None
        sync_state.cursor = "missing-cursor"

    provider = FakeTransactionProvider({})
    with pytest.raises(UnknownProviderCursorError, match="missing-cursor"):
        _processor(event_processing_database, provider).process(
            event_processing_database.event_id
        )

    with event_processing_database.session_factory() as session:
        event = session.get(InboundEventModel, event_processing_database.event_id)
        assert event is not None
        assert event.status == "failed"
        assert event.last_error_code == "provider_error"
        assert "missing-cursor" not in event.last_error_code


@pytest.mark.integration
def test_missing_event_and_provider_configuration_are_explicit(
    event_processing_database: EventProcessingDatabase,
) -> None:
    processor = InboundEventProcessor(
        session_factory=event_processing_database.session_factory,
        providers={},
    )

    with pytest.raises(InboundEventNotFoundError, match="does not exist"):
        processor.process(uuid4())
    with pytest.raises(ProviderNotConfiguredError, match="not configured"):
        processor.process(event_processing_database.event_id)


@pytest.mark.integration
def test_unsupported_event_type_remains_pending(
    event_processing_database: EventProcessingDatabase,
) -> None:
    with event_processing_database.session_factory() as session, session.begin():
        event = session.get(InboundEventModel, event_processing_database.event_id)
        assert event is not None
        event.event_type = "accounts.updated"

    with pytest.raises(UnsupportedInboundEventTypeError, match="not supported"):
        _processor(
            event_processing_database,
            _provider_with_one_transaction(),
        ).process(event_processing_database.event_id)

    with event_processing_database.session_factory() as session:
        event = session.get(InboundEventModel, event_processing_database.event_id)
        assert event is not None
        assert event.status == "pending"
