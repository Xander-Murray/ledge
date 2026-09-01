from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, selectinload

from domain.ledger import (
    TransactionConflictError,
    TransactionNotFoundError,
    TransactionStateError,
)
from domain.models import Transaction, TransactionRemoval
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


class InjectedPersistenceFailure(RuntimeError):
    """Failure raised by a test between repository flushes."""


def removal_for(transaction: Transaction) -> TransactionRemoval:
    return TransactionRemoval(
        account_id=transaction.account_id,
        provider_transaction_id=transaction.provider_transaction_id,
    )


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


@pytest.mark.integration
def test_add_transaction_rolls_back_when_sealing_fails(
    repository_database: RepositoryDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transaction = Transaction(
        account_id=repository_database.financial_account_id,
        provider_transaction_id="provider-transaction-1",
        amount_cents=1_250,
        description="Neighborhood Market",
    )

    with Session(repository_database.engine) as session:
        real_flush = session.flush
        flush_calls = 0

        def fail_on_second_flush(objects: Sequence[Any] | None = None) -> None:
            nonlocal flush_calls
            flush_calls += 1
            if flush_calls == 2:
                raise InjectedPersistenceFailure("journal sealing failed")
            real_flush(objects)

        monkeypatch.setattr(session, "flush", fail_on_second_flush)

        with pytest.raises(InjectedPersistenceFailure), session.begin():
            LedgerRepository(session).add_transaction(
                user_id=repository_database.user_id,
                transaction=transaction,
            )

    with Session(repository_database.engine) as session:
        assert session.scalar(select(func.count(ExternalTransactionModel.id))) == 0
        assert session.scalar(select(func.count(JournalEntryModel.id))) == 0
        assert session.scalar(select(func.count(PostingModel.id))) == 0


@pytest.mark.integration
def test_modify_transaction_appends_reversal_and_replacement_journals(
    repository_database: RepositoryDatabase,
) -> None:
    original = Transaction(
        account_id=repository_database.financial_account_id,
        provider_transaction_id="provider-transaction-1",
        amount_cents=1_250,
        description="Neighborhood Market",
    )
    modified = Transaction(
        account_id=original.account_id,
        provider_transaction_id=original.provider_transaction_id,
        amount_cents=1_400,
        description="Neighborhood Market with tip",
    )

    with Session(repository_database.engine) as session, session.begin():
        external_transaction_id = LedgerRepository(session).add_transaction(
            user_id=repository_database.user_id,
            transaction=original,
        )

    with Session(repository_database.engine) as session, session.begin():
        modified_transaction_id = LedgerRepository(session).modify_transaction(
            user_id=repository_database.user_id,
            transaction=modified,
        )

    assert modified_transaction_id == external_transaction_id
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
        assert persisted_transaction.amount_cents == modified.amount_cents
        assert persisted_transaction.description == modified.description
        assert len(persisted_transaction.journal_entries) == 3

        reversal = next(
            entry
            for entry in persisted_transaction.journal_entries
            if entry.reversal_of_entry_id is not None
        )
        original_journal = next(
            entry
            for entry in persisted_transaction.journal_entries
            if entry.id == reversal.reversal_of_entry_id
        )
        replacement = next(
            entry
            for entry in persisted_transaction.journal_entries
            if entry.reversal_of_entry_id is None and entry.id != original_journal.id
        )

        assert all(
            entry.sealed_at is not None
            for entry in persisted_transaction.journal_entries
        )
        assert reversal.description == f"Reversal of {original.description}"
        assert replacement.description == modified.description

        original_postings = sorted(
            original_journal.postings,
            key=lambda posting: posting.line_number,
        )
        reversal_postings = sorted(
            reversal.postings,
            key=lambda posting: posting.line_number,
        )
        replacement_postings = sorted(
            replacement.postings,
            key=lambda posting: posting.line_number,
        )

        assert [posting.amount_cents for posting in original_postings] == [
            1_250,
            -1_250,
        ]
        assert [posting.amount_cents for posting in reversal_postings] == [
            -1_250,
            1_250,
        ]
        assert [posting.amount_cents for posting in replacement_postings] == [
            1_400,
            -1_400,
        ]
        assert session.scalar(select(func.count(PostingModel.id))) == 6


@pytest.mark.integration
def test_identical_duplicate_modification_is_idempotent(
    repository_database: RepositoryDatabase,
) -> None:
    original = Transaction(
        account_id=repository_database.financial_account_id,
        provider_transaction_id="provider-transaction-1",
        amount_cents=1_250,
        description="Neighborhood Market",
    )
    modified = Transaction(
        account_id=original.account_id,
        provider_transaction_id=original.provider_transaction_id,
        amount_cents=1_400,
        description="Neighborhood Market with tip",
    )

    with Session(repository_database.engine) as session, session.begin():
        original_id = LedgerRepository(session).add_transaction(
            user_id=repository_database.user_id,
            transaction=original,
        )

    with Session(repository_database.engine) as session, session.begin():
        first_modification_id = LedgerRepository(session).modify_transaction(
            user_id=repository_database.user_id,
            transaction=modified,
        )

    with Session(repository_database.engine) as session, session.begin():
        duplicate_modification_id = LedgerRepository(session).modify_transaction(
            user_id=repository_database.user_id,
            transaction=modified,
        )

    assert first_modification_id == original_id
    assert duplicate_modification_id == original_id
    with Session(repository_database.engine) as session:
        persisted_transaction = session.get(ExternalTransactionModel, original_id)
        assert persisted_transaction is not None
        assert persisted_transaction.amount_cents == modified.amount_cents
        assert persisted_transaction.description == modified.description
        assert session.scalar(select(func.count(JournalEntryModel.id))) == 3
        assert session.scalar(select(func.count(PostingModel.id))) == 6


@pytest.mark.integration
def test_modify_transaction_rejects_missing_transaction(
    repository_database: RepositoryDatabase,
) -> None:
    transaction = Transaction(
        account_id=repository_database.financial_account_id,
        provider_transaction_id="missing-transaction",
        amount_cents=1_400,
        description="Neighborhood Market with tip",
    )

    with (
        pytest.raises(TransactionNotFoundError, match="does not exist"),
        Session(repository_database.engine) as session,
        session.begin(),
    ):
        LedgerRepository(session).modify_transaction(
            user_id=repository_database.user_id,
            transaction=transaction,
        )

    with Session(repository_database.engine) as session:
        assert session.scalar(select(func.count(ExternalTransactionModel.id))) == 0
        assert session.scalar(select(func.count(JournalEntryModel.id))) == 0
        assert session.scalar(select(func.count(PostingModel.id))) == 0


@pytest.mark.integration
def test_modify_transaction_rejects_removed_transaction_without_changes(
    repository_database: RepositoryDatabase,
) -> None:
    original = Transaction(
        account_id=repository_database.financial_account_id,
        provider_transaction_id="provider-transaction-1",
        amount_cents=1_250,
        description="Neighborhood Market",
    )
    modified = Transaction(
        account_id=original.account_id,
        provider_transaction_id=original.provider_transaction_id,
        amount_cents=1_400,
        description="Neighborhood Market with tip",
    )

    with Session(repository_database.engine) as session, session.begin():
        external_transaction_id = LedgerRepository(session).add_transaction(
            user_id=repository_database.user_id,
            transaction=original,
        )

    with Session(repository_database.engine) as session, session.begin():
        LedgerRepository(session).remove_transaction(
            user_id=repository_database.user_id,
            removal=removal_for(original),
        )

    with (
        pytest.raises(TransactionStateError, match="cannot be modified"),
        Session(repository_database.engine) as session,
        session.begin(),
    ):
        LedgerRepository(session).modify_transaction(
            user_id=repository_database.user_id,
            transaction=modified,
        )

    with Session(repository_database.engine) as session:
        persisted_transaction = session.get(
            ExternalTransactionModel, external_transaction_id
        )
        assert persisted_transaction is not None
        assert persisted_transaction.status == "removed"
        assert persisted_transaction.amount_cents == original.amount_cents
        assert persisted_transaction.description == original.description
        assert session.scalar(select(func.count(JournalEntryModel.id))) == 2
        assert session.scalar(select(func.count(PostingModel.id))) == 4


@pytest.mark.integration
def test_modify_transaction_rolls_back_projection_and_journals_when_sealing_fails(
    repository_database: RepositoryDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = Transaction(
        account_id=repository_database.financial_account_id,
        provider_transaction_id="provider-transaction-1",
        amount_cents=1_250,
        description="Neighborhood Market",
    )
    modified = Transaction(
        account_id=original.account_id,
        provider_transaction_id=original.provider_transaction_id,
        amount_cents=1_400,
        description="Neighborhood Market with tip",
    )

    with Session(repository_database.engine) as session, session.begin():
        external_transaction_id = LedgerRepository(session).add_transaction(
            user_id=repository_database.user_id,
            transaction=original,
        )

    with Session(repository_database.engine) as session:
        real_flush = session.flush
        flush_calls = 0

        def fail_on_second_flush(objects: Sequence[Any] | None = None) -> None:
            nonlocal flush_calls
            flush_calls += 1
            if flush_calls == 2:
                raise InjectedPersistenceFailure("replacement sealing failed")
            real_flush(objects)

        monkeypatch.setattr(session, "flush", fail_on_second_flush)

        with pytest.raises(InjectedPersistenceFailure), session.begin():
            LedgerRepository(session).modify_transaction(
                user_id=repository_database.user_id,
                transaction=modified,
            )

    with Session(repository_database.engine) as session:
        persisted_transaction = session.get(
            ExternalTransactionModel, external_transaction_id
        )
        assert persisted_transaction is not None
        assert persisted_transaction.status == "active"
        assert persisted_transaction.amount_cents == original.amount_cents
        assert persisted_transaction.description == original.description
        assert session.scalar(select(func.count(JournalEntryModel.id))) == 1
        assert session.scalar(select(func.count(PostingModel.id))) == 2


@pytest.mark.integration
def test_remove_transaction_appends_reversal_and_marks_projection_removed(
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

    with Session(repository_database.engine) as session, session.begin():
        removed_transaction_id = LedgerRepository(session).remove_transaction(
            user_id=repository_database.user_id,
            removal=removal_for(transaction),
        )

    assert removed_transaction_id == external_transaction_id
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
        assert persisted_transaction.status == "removed"
        assert persisted_transaction.amount_cents == transaction.amount_cents
        assert persisted_transaction.description == transaction.description
        assert len(persisted_transaction.journal_entries) == 2

        original = next(
            entry
            for entry in persisted_transaction.journal_entries
            if entry.reversal_of_entry_id is None
        )
        reversal = next(
            entry
            for entry in persisted_transaction.journal_entries
            if entry.reversal_of_entry_id is not None
        )

        assert original.sealed_at is not None
        assert reversal.sealed_at is not None
        assert reversal.reversal_of_entry_id == original.id
        assert reversal.description == f"Reversal of {transaction.description}"

        original_postings = sorted(
            original.postings,
            key=lambda posting: posting.line_number,
        )
        reversal_postings = sorted(
            reversal.postings,
            key=lambda posting: posting.line_number,
        )
        assert [posting.amount_cents for posting in original_postings] == [
            1_250,
            -1_250,
        ]
        assert [posting.amount_cents for posting in reversal_postings] == [
            -1_250,
            1_250,
        ]
        assert (
            sum(
                posting.amount_cents
                for entry in persisted_transaction.journal_entries
                for posting in entry.postings
            )
            == 0
        )


@pytest.mark.integration
def test_remove_transaction_reverses_current_journal_after_modification(
    repository_database: RepositoryDatabase,
) -> None:
    original = Transaction(
        account_id=repository_database.financial_account_id,
        provider_transaction_id="provider-transaction-1",
        amount_cents=1_250,
        description="Neighborhood Market",
    )
    modified = Transaction(
        account_id=original.account_id,
        provider_transaction_id=original.provider_transaction_id,
        amount_cents=1_400,
        description="Neighborhood Market with tip",
    )

    with Session(repository_database.engine) as session, session.begin():
        external_transaction_id = LedgerRepository(session).add_transaction(
            user_id=repository_database.user_id,
            transaction=original,
        )

    with Session(repository_database.engine) as session, session.begin():
        LedgerRepository(session).modify_transaction(
            user_id=repository_database.user_id,
            transaction=modified,
        )

    with Session(repository_database.engine) as session, session.begin():
        LedgerRepository(session).remove_transaction(
            user_id=repository_database.user_id,
            removal=removal_for(modified),
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
        assert persisted_transaction.status == "removed"
        assert len(persisted_transaction.journal_entries) == 4
        replacement = next(
            entry
            for entry in persisted_transaction.journal_entries
            if entry.reversal_of_entry_id is None
            and entry.description == modified.description
        )
        removal_reversal = next(
            entry
            for entry in persisted_transaction.journal_entries
            if entry.reversal_of_entry_id == replacement.id
        )
        assert [
            posting.amount_cents
            for posting in sorted(
                removal_reversal.postings,
                key=lambda posting: posting.line_number,
            )
        ] == [-1_400, 1_400]
        assert (
            sum(
                posting.amount_cents
                for entry in persisted_transaction.journal_entries
                for posting in entry.postings
                if posting.ledger_account == "suspense:unclassified"
            )
            == 0
        )


@pytest.mark.integration
def test_identical_duplicate_removal_is_idempotent(
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
        first_removal_id = LedgerRepository(session).remove_transaction(
            user_id=repository_database.user_id,
            removal=removal_for(transaction),
        )

    with Session(repository_database.engine) as session, session.begin():
        duplicate_removal_id = LedgerRepository(session).remove_transaction(
            user_id=repository_database.user_id,
            removal=removal_for(transaction),
        )

    assert first_removal_id == original_id
    assert duplicate_removal_id == original_id
    with Session(repository_database.engine) as session:
        assert session.scalar(select(func.count(ExternalTransactionModel.id))) == 1
        assert session.scalar(select(func.count(JournalEntryModel.id))) == 2
        assert session.scalar(select(func.count(PostingModel.id))) == 4


@pytest.mark.integration
def test_remove_transaction_rejects_missing_transaction(
    repository_database: RepositoryDatabase,
) -> None:
    transaction = Transaction(
        account_id=repository_database.financial_account_id,
        provider_transaction_id="missing-transaction",
        amount_cents=1_250,
        description="Neighborhood Market",
    )

    with (
        pytest.raises(TransactionNotFoundError, match="does not exist"),
        Session(repository_database.engine) as session,
        session.begin(),
    ):
        LedgerRepository(session).remove_transaction(
            user_id=repository_database.user_id,
            removal=removal_for(transaction),
        )

    with Session(repository_database.engine) as session:
        assert session.scalar(select(func.count(ExternalTransactionModel.id))) == 0
        assert session.scalar(select(func.count(JournalEntryModel.id))) == 0
        assert session.scalar(select(func.count(PostingModel.id))) == 0


@pytest.mark.integration
def test_remove_transaction_rejects_conflicting_account_without_changes(
    repository_database: RepositoryDatabase,
) -> None:
    original = Transaction(
        account_id=repository_database.financial_account_id,
        provider_transaction_id="provider-transaction-1",
        amount_cents=1_250,
        description="Neighborhood Market",
    )
    conflicting = TransactionRemoval(
        account_id=uuid4(),
        provider_transaction_id=original.provider_transaction_id,
    )

    with Session(repository_database.engine) as session, session.begin():
        external_transaction_id = LedgerRepository(session).add_transaction(
            user_id=repository_database.user_id,
            transaction=original,
        )

    with (
        pytest.raises(TransactionConflictError, match="removal account differs"),
        Session(repository_database.engine) as session,
        session.begin(),
    ):
        LedgerRepository(session).remove_transaction(
            user_id=repository_database.user_id,
            removal=conflicting,
        )

    with Session(repository_database.engine) as session:
        persisted_transaction = session.get(
            ExternalTransactionModel, external_transaction_id
        )
        assert persisted_transaction is not None
        assert persisted_transaction.status == "active"
        assert session.scalar(select(func.count(JournalEntryModel.id))) == 1
        assert session.scalar(select(func.count(PostingModel.id))) == 2


@pytest.mark.integration
def test_remove_transaction_rolls_back_status_and_reversal_when_sealing_fails(
    repository_database: RepositoryDatabase,
    monkeypatch: pytest.MonkeyPatch,
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
        real_flush = session.flush
        flush_calls = 0

        def fail_on_second_flush(objects: Sequence[Any] | None = None) -> None:
            nonlocal flush_calls
            flush_calls += 1
            if flush_calls == 2:
                raise InjectedPersistenceFailure("reversal sealing failed")
            real_flush(objects)

        monkeypatch.setattr(session, "flush", fail_on_second_flush)

        with pytest.raises(InjectedPersistenceFailure), session.begin():
            LedgerRepository(session).remove_transaction(
                user_id=repository_database.user_id,
                removal=removal_for(transaction),
            )

    with Session(repository_database.engine) as session:
        persisted_transaction = session.get(
            ExternalTransactionModel, external_transaction_id
        )
        assert persisted_transaction is not None
        assert persisted_transaction.status == "active"
        assert session.scalar(select(func.count(JournalEntryModel.id))) == 1
        assert session.scalar(select(func.count(PostingModel.id))) == 2
