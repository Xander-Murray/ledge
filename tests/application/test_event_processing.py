from __future__ import annotations

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
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
from providers.base import TransactionSyncPage
from providers.fake import FakeTransactionProvider

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
    """Pause the first fetch so a duplicate worker can contend for the row."""

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
    provider: FakeTransactionProvider | CoordinatedProvider,
) -> InboundEventProcessor:
    return InboundEventProcessor(
        session_factory=database.session_factory,
        providers={PROVIDER_NAME: provider},
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
    provider = _provider_with_one_transaction()

    result = _processor(event_processing_database, provider).process(
        event_processing_database.event_id
    )

    assert result.event_id == event_processing_database.event_id
    assert result.already_processed is False
    assert result.synchronization is not None
    assert result.synchronization.ending_cursor == "cursor-1"
    assert result.synchronization.added_count == 1
    assert provider.requested_cursors == [None]

    with event_processing_database.session_factory() as session:
        event = session.get(InboundEventModel, event_processing_database.event_id)
        sync_state = session.get(
            TransactionSyncStateModel,
            event_processing_database.sync_state_id,
        )

        assert event is not None
        assert event.status == "processed"
        assert event.processed_at is not None
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
    assert result.synchronization is None
    assert provider.requested_cursors == [None]


@pytest.mark.integration
def test_failed_synchronization_rolls_event_back_for_retry(
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
        assert event.status == "pending"
        assert event.processed_at is None
        assert sync_state is not None
        assert sync_state.cursor is None
        assert session.scalar(select(func.count(ExternalTransactionModel.id))) == 0

    retry_result = _processor(
        event_processing_database,
        _provider_with_one_transaction(),
    ).process(event_processing_database.event_id)

    assert retry_result.already_processed is False
    assert retry_result.synchronization is not None
    assert retry_result.synchronization.ending_cursor == "cursor-1"


@pytest.mark.integration
def test_duplicate_workers_serialize_and_synchronize_only_once(
    event_processing_database: EventProcessingDatabase,
) -> None:
    provider = CoordinatedProvider()
    processor = _processor(event_processing_database, provider)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_worker = executor.submit(
            processor.process,
            event_processing_database.event_id,
        )
        assert provider.fetch_started.wait(timeout=5)
        second_worker = executor.submit(
            processor.process,
            event_processing_database.event_id,
        )
        provider.release_fetch.set()
        results = (first_worker.result(timeout=5), second_worker.result(timeout=5))

    assert provider.call_count == 1
    assert sorted(result.already_processed for result in results) == [False, True]


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
