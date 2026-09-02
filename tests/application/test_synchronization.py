from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from application.synchronization import (
    ProviderPaginationError,
    SyncCursorConflictError,
    SyncResult,
    SyncStateNotFoundError,
    TransactionSynchronizer,
)
from domain.ledger import TransactionNotFoundError
from domain.models import Transaction
from persistence.database import create_session_factory
from persistence.models import (
    ExternalTransactionModel,
    FinancialAccountModel,
    JournalEntryModel,
    PostingModel,
    TransactionSyncStateModel,
    UserModel,
)
from providers.base import TransactionProvider, TransactionSyncPage
from providers.fake import FakeTransactionProvider

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROVIDER_FIXTURE = (
    PROJECT_ROOT / "tests" / "fixtures" / "providers" / "transaction_sync_pages.json"
)
ACCOUNT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
PROVIDER_NAME = "fake"
PROVIDER_CONNECTION_ID = "connection-1"


@dataclass(frozen=True)
class SynchronizationDatabase:
    engine: Engine
    session_factory: sessionmaker[Session]
    user_id: UUID
    sync_state_id: UUID


class CursorAdvancingProvider:
    """Advance database state once while the synchronizer fetches pages."""

    def __init__(
        self,
        provider: TransactionProvider,
        advance_cursor: Callable[[], None],
    ) -> None:
        self._provider = provider
        self._advance_cursor = advance_cursor
        self._advanced = False

    def fetch_transaction_updates(
        self,
        cursor: str | None,
    ) -> TransactionSyncPage:
        page = self._provider.fetch_transaction_updates(cursor)
        if not self._advanced:
            self._advance_cursor()
            self._advanced = True
        return page


def _get_test_database_url() -> str:
    database_url = os.environ.get("LEDGE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("Set LEDGE_TEST_DATABASE_URL to run synchronization tests")

    database_name = make_url(database_url).database
    if database_name is None or not database_name.endswith("_test"):
        pytest.fail("Synchronization tests require a database ending in '_test'")

    return database_url


@pytest.fixture
def synchronization_database(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[SynchronizationDatabase]:
    database_url = _get_test_database_url()
    monkeypatch.setenv("LEDGE_DATABASE_URL", database_url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))

    command.downgrade(config, "base")
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)
    user_id = uuid4()
    sync_state_id = uuid4()
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

    try:
        yield SynchronizationDatabase(
            engine=engine,
            session_factory=session_factory,
            user_id=user_id,
            sync_state_id=sync_state_id,
        )
    finally:
        engine.dispose()
        command.downgrade(config, "base")


def _synchronizer(
    database: SynchronizationDatabase,
    provider: TransactionProvider,
) -> TransactionSynchronizer:
    return TransactionSynchronizer(
        session_factory=database.session_factory,
        provider=provider,
    )


def _synchronize(
    synchronizer: TransactionSynchronizer,
    user_id: UUID,
) -> SyncResult:
    return synchronizer.synchronize(
        user_id=user_id,
        provider_name=PROVIDER_NAME,
        provider_connection_id=PROVIDER_CONNECTION_ID,
    )


@pytest.mark.integration
def test_complete_provider_update_and_cursor_commit_together(
    synchronization_database: SynchronizationDatabase,
) -> None:
    provider = FakeTransactionProvider.from_json(PROVIDER_FIXTURE)

    result = _synchronize(
        _synchronizer(synchronization_database, provider),
        synchronization_database.user_id,
    )

    assert result.starting_cursor is None
    assert result.ending_cursor == "cursor-2"
    assert result.pages_fetched == 2
    assert result.added_count == 2
    assert result.modified_count == 1
    assert result.removed_count == 1
    assert provider.requested_cursors == [None, "cursor-1"]

    with synchronization_database.session_factory() as session:
        sync_state = session.get(
            TransactionSyncStateModel,
            synchronization_database.sync_state_id,
        )
        transactions = {
            transaction.provider_transaction_id: transaction
            for transaction in session.scalars(select(ExternalTransactionModel))
        }

        assert sync_state is not None
        assert sync_state.cursor == "cursor-2"
        assert set(transactions) == {"grocery-1", "refund-1"}
        assert transactions["grocery-1"].amount_cents == 1_400
        assert transactions["grocery-1"].status == "active"
        assert transactions["refund-1"].amount_cents == -500
        assert transactions["refund-1"].status == "removed"
        assert session.scalar(select(func.count(JournalEntryModel.id))) == 5
        assert session.scalar(select(func.count(PostingModel.id))) == 10


@pytest.mark.integration
def test_repeated_sync_from_current_cursor_is_a_no_op(
    synchronization_database: SynchronizationDatabase,
) -> None:
    provider = FakeTransactionProvider.from_json(PROVIDER_FIXTURE)
    synchronizer = _synchronizer(synchronization_database, provider)
    _synchronize(synchronizer, synchronization_database.user_id)

    result = _synchronize(synchronizer, synchronization_database.user_id)

    assert result.starting_cursor == "cursor-2"
    assert result.ending_cursor == "cursor-2"
    assert result.pages_fetched == 1
    assert result.added_count == 0
    assert result.modified_count == 0
    assert result.removed_count == 0
    assert provider.requested_cursors == [None, "cursor-1", "cursor-2"]
    with synchronization_database.session_factory() as session:
        assert session.scalar(select(func.count(ExternalTransactionModel.id))) == 2
        assert session.scalar(select(func.count(JournalEntryModel.id))) == 5
        assert session.scalar(select(func.count(PostingModel.id))) == 10


@pytest.mark.integration
def test_failed_change_rolls_back_batch_and_cursor_then_retry_succeeds(
    synchronization_database: SynchronizationDatabase,
) -> None:
    added = Transaction(
        account_id=ACCOUNT_ID,
        provider_transaction_id="grocery-1",
        amount_cents=1_250,
        description="Neighborhood Market",
    )
    missing_modification = Transaction(
        account_id=ACCOUNT_ID,
        provider_transaction_id="missing-transaction",
        amount_cents=1_400,
        description="Unknown transaction",
    )
    provider = FakeTransactionProvider(
        {
            None: TransactionSyncPage(
                added=(added,),
                modified=(missing_modification,),
                removed=(),
                next_cursor="failed-cursor",
                has_more=False,
            )
        }
    )

    with pytest.raises(TransactionNotFoundError, match="does not exist"):
        _synchronize(
            _synchronizer(synchronization_database, provider),
            synchronization_database.user_id,
        )

    with synchronization_database.session_factory() as session:
        sync_state = session.get(
            TransactionSyncStateModel,
            synchronization_database.sync_state_id,
        )
        assert sync_state is not None
        assert sync_state.cursor is None
        assert session.scalar(select(func.count(ExternalTransactionModel.id))) == 0
        assert session.scalar(select(func.count(JournalEntryModel.id))) == 0
        assert session.scalar(select(func.count(PostingModel.id))) == 0

    retry_provider = FakeTransactionProvider(
        {
            None: TransactionSyncPage(
                added=(added,),
                modified=(),
                removed=(),
                next_cursor="recovered-cursor",
                has_more=False,
            )
        }
    )
    retry_result = _synchronize(
        _synchronizer(synchronization_database, retry_provider),
        synchronization_database.user_id,
    )

    assert retry_result.starting_cursor is None
    assert retry_result.ending_cursor == "recovered-cursor"
    with synchronization_database.session_factory() as session:
        sync_state = session.get(
            TransactionSyncStateModel,
            synchronization_database.sync_state_id,
        )
        assert sync_state is not None
        assert sync_state.cursor == "recovered-cursor"
        assert session.scalar(select(func.count(ExternalTransactionModel.id))) == 1
        assert session.scalar(select(func.count(JournalEntryModel.id))) == 1
        assert session.scalar(select(func.count(PostingModel.id))) == 2


@pytest.mark.integration
def test_cursor_change_during_fetch_rejects_stale_batch(
    synchronization_database: SynchronizationDatabase,
) -> None:
    def advance_cursor() -> None:
        with synchronization_database.session_factory() as session, session.begin():
            sync_state = session.get(
                TransactionSyncStateModel,
                synchronization_database.sync_state_id,
            )
            assert sync_state is not None
            sync_state.cursor = "concurrent-cursor"

    provider = CursorAdvancingProvider(
        FakeTransactionProvider.from_json(PROVIDER_FIXTURE),
        advance_cursor,
    )

    with pytest.raises(SyncCursorConflictError, match="advanced"):
        _synchronize(
            _synchronizer(synchronization_database, provider),
            synchronization_database.user_id,
        )

    with synchronization_database.session_factory() as session:
        sync_state = session.get(
            TransactionSyncStateModel,
            synchronization_database.sync_state_id,
        )
        assert sync_state is not None
        assert sync_state.cursor == "concurrent-cursor"
        assert session.scalar(select(func.count(ExternalTransactionModel.id))) == 0
        assert session.scalar(select(func.count(JournalEntryModel.id))) == 0


@pytest.mark.integration
def test_repeated_pagination_cursor_is_rejected_before_database_writes(
    synchronization_database: SynchronizationDatabase,
) -> None:
    first_page = TransactionSyncPage(
        added=(),
        modified=(),
        removed=(),
        next_cursor="repeated-cursor",
        has_more=True,
    )
    provider = FakeTransactionProvider(
        {
            None: first_page,
            "repeated-cursor": first_page,
        }
    )

    with pytest.raises(ProviderPaginationError, match="repeated cursor"):
        _synchronize(
            _synchronizer(synchronization_database, provider),
            synchronization_database.user_id,
        )

    with synchronization_database.session_factory() as session:
        sync_state = session.get(
            TransactionSyncStateModel,
            synchronization_database.sync_state_id,
        )
        assert sync_state is not None
        assert sync_state.cursor is None
        assert session.scalar(select(func.count(ExternalTransactionModel.id))) == 0


@pytest.mark.integration
def test_unknown_sync_state_is_rejected_before_provider_fetch(
    synchronization_database: SynchronizationDatabase,
) -> None:
    provider = FakeTransactionProvider.from_json(PROVIDER_FIXTURE)

    with pytest.raises(SyncStateNotFoundError, match="does not exist"):
        _synchronize(
            _synchronizer(synchronization_database, provider),
            uuid4(),
        )

    assert provider.requested_cursors == []
