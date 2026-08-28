from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, selectinload

from domain.ledger import TransactionConflictError
from domain.models import Transaction
from persistence.models import (
    ExternalTransactionModel,
    FinancialAccountModel,
    JournalEntryModel,
    PostingModel,
    UserModel,
)
from persistence.repository import LedgerRepository

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class RepositoryDatabase:
    engine: Engine
    user_id: UUID
    financial_account_id: UUID


def _get_test_database_url() -> str:
    database_url = os.environ.get("LEDGE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("Set LEDGE_TEST_DATABASE_URL to run repository tests")

    database_name = make_url(database_url).database
    if database_name is None or not database_name.endswith("_test"):
        pytest.fail("Repository tests require a database ending in '_test'")

    return database_url


@pytest.fixture
def repository_database(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[RepositoryDatabase]:
    database_url = _get_test_database_url()
    monkeypatch.setenv("LEDGE_DATABASE_URL", database_url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))

    command.downgrade(config, "base")
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    user_id = uuid4()
    financial_account_id = uuid4()

    with Session(engine) as session, session.begin():
        session.add(UserModel(id=user_id))
        session.add(
            FinancialAccountModel(
                id=financial_account_id,
                user_id=user_id,
                name="Checking",
                account_type="checking",
            )
        )

    try:
        yield RepositoryDatabase(
            engine=engine,
            user_id=user_id,
            financial_account_id=financial_account_id,
        )
    finally:
        engine.dispose()
        command.downgrade(config, "base")


@pytest.mark.integration
def test_add_transaction_persists_one_complete_sealed_journal(
    repository_database: RepositoryDatabase,
) -> None:
    transaction = Transaction(
        account_id=repository_database.financial_account_id,
        provider_transaction_id="provider-transaction-1",
        amount_cents=1_250,
        description="Neighborhood Market",
    )

    with Session(repository_database.engine) as session, session.begin():
        external_transaction_id = LedgerRepository(session).add_transaction(
            user_id=repository_database.user_id,
            transaction=transaction,
        )

    with Session(repository_database.engine) as session:
        persisted_transaction = session.scalar(
            select(ExternalTransactionModel)
            .where(ExternalTransactionModel.id == external_transaction_id)
            .options(
                selectinload(ExternalTransactionModel.journal_entries).selectinload(
                    JournalEntryModel.postings
                )
            )
        )

        assert persisted_transaction is not None
        assert persisted_transaction.user_id == repository_database.user_id
        assert (
            persisted_transaction.financial_account_id
            == repository_database.financial_account_id
        )
        assert persisted_transaction.provider_transaction_id == (
            transaction.provider_transaction_id
        )
        assert persisted_transaction.amount_cents == transaction.amount_cents
        assert persisted_transaction.description == transaction.description
        assert persisted_transaction.status == "active"

        assert len(persisted_transaction.journal_entries) == 1
        journal_entry = persisted_transaction.journal_entries[0]
        assert journal_entry.description == transaction.description
        assert journal_entry.reversal_of_entry_id is None
        assert journal_entry.sealed_at is not None

        postings = sorted(
            journal_entry.postings,
            key=lambda posting: posting.line_number,
        )
        assert [
            (posting.line_number, posting.ledger_account, posting.amount_cents)
            for posting in postings
        ] == [
            (0, "suspense:unclassified", 1_250),
            (
                1,
                f"financial:{repository_database.financial_account_id}",
                -1_250,
            ),
        ]


@pytest.mark.integration
def test_identical_duplicate_returns_existing_transaction_without_new_rows(
    repository_database: RepositoryDatabase,
) -> None:
    transaction = Transaction(
        account_id=repository_database.financial_account_id,
        provider_transaction_id="provider-transaction-1",
        amount_cents=1_250,
        description="Neighborhood Market",
    )

    with Session(repository_database.engine) as session, session.begin():
        original_id = LedgerRepository(session).add_transaction(
            user_id=repository_database.user_id,
            transaction=transaction,
        )

    with Session(repository_database.engine) as session, session.begin():
        duplicate_id = LedgerRepository(session).add_transaction(
            user_id=repository_database.user_id,
            transaction=transaction,
        )

    assert duplicate_id == original_id
    with Session(repository_database.engine) as session:
        assert session.scalar(select(func.count(ExternalTransactionModel.id))) == 1
        assert session.scalar(select(func.count(JournalEntryModel.id))) == 1
        assert session.scalar(select(func.count(PostingModel.id))) == 2


@pytest.mark.integration
def test_conflicting_duplicate_is_rejected_without_changing_persisted_rows(
    repository_database: RepositoryDatabase,
) -> None:
    original = Transaction(
        account_id=repository_database.financial_account_id,
        provider_transaction_id="provider-transaction-1",
        amount_cents=1_250,
        description="Neighborhood Market",
    )
    conflicting = Transaction(
        account_id=original.account_id,
        provider_transaction_id=original.provider_transaction_id,
        amount_cents=1_400,
        description=original.description,
    )

    with Session(repository_database.engine) as session, session.begin():
        original_id = LedgerRepository(session).add_transaction(
            user_id=repository_database.user_id,
            transaction=original,
        )

    with (
        pytest.raises(
            TransactionConflictError,
            match="already exists with different data",
        ),
        Session(repository_database.engine) as session,
        session.begin(),
    ):
        LedgerRepository(session).add_transaction(
            user_id=repository_database.user_id,
            transaction=conflicting,
        )

    with Session(repository_database.engine) as session:
        persisted_transaction = session.get(ExternalTransactionModel, original_id)

        assert persisted_transaction is not None
        assert persisted_transaction.amount_cents == original.amount_cents
        assert persisted_transaction.description == original.description
        assert session.scalar(select(func.count(ExternalTransactionModel.id))) == 1
        assert session.scalar(select(func.count(JournalEntryModel.id))) == 1
        assert session.scalar(select(func.count(PostingModel.id))) == 2
