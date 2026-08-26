from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class JournalFixture:
    engine: Engine
    external_transaction_id: UUID
    journal_entry_id: UUID


def _get_test_database_url() -> str:
    database_url = os.environ.get("LEDGE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("Set LEDGE_TEST_DATABASE_URL to run journal guard tests")

    database_name = make_url(database_url).database
    if database_name is None or not database_name.endswith("_test"):
        pytest.fail("Journal guard tests require a database ending in '_test'")

    return database_url


@pytest.fixture
def journal_fixture(monkeypatch: pytest.MonkeyPatch) -> Iterator[JournalFixture]:
    database_url = _get_test_database_url()
    monkeypatch.setenv("LEDGE_DATABASE_URL", database_url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))

    command.downgrade(config, "base")
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    user_id = uuid4()
    financial_account_id = uuid4()
    external_transaction_id = uuid4()
    journal_entry_id = uuid4()

    with engine.begin() as connection:
        connection.execute(text("INSERT INTO users (id) VALUES (:id)"), {"id": user_id})
        connection.execute(
            text(
                """
                INSERT INTO financial_accounts (id, user_id, name, account_type)
                VALUES (:id, :user_id, 'Checking', 'checking')
                """
            ),
            {"id": financial_account_id, "user_id": user_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO external_transactions (
                    id,
                    user_id,
                    financial_account_id,
                    provider_transaction_id,
                    amount_cents,
                    description
                )
                VALUES (
                    :id,
                    :user_id,
                    :financial_account_id,
                    'provider-transaction-1',
                    1250,
                    'Neighborhood Market'
                )
                """
            ),
            {
                "id": external_transaction_id,
                "user_id": user_id,
                "financial_account_id": financial_account_id,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO journal_entries (id, external_transaction_id, description)
                VALUES (:id, :external_transaction_id, 'Transaction added')
                """
            ),
            {
                "id": journal_entry_id,
                "external_transaction_id": external_transaction_id,
            },
        )

    try:
        yield JournalFixture(
            engine=engine,
            external_transaction_id=external_transaction_id,
            journal_entry_id=journal_entry_id,
        )
    finally:
        engine.dispose()
        command.downgrade(config, "base")


def _insert_posting(
    fixture: JournalFixture,
    *,
    line_number: int,
    ledger_account: str,
    amount_cents: int,
) -> None:
    with fixture.engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO postings (
                    journal_entry_id,
                    line_number,
                    ledger_account,
                    amount_cents
                )
                VALUES (
                    :journal_entry_id,
                    :line_number,
                    :ledger_account,
                    :amount_cents
                )
                """
            ),
            {
                "journal_entry_id": fixture.journal_entry_id,
                "line_number": line_number,
                "ledger_account": ledger_account,
                "amount_cents": amount_cents,
            },
        )


def _seal_journal(fixture: JournalFixture) -> None:
    with fixture.engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE journal_entries
                SET sealed_at = now()
                WHERE id = :journal_entry_id
                """
            ),
            {"journal_entry_id": fixture.journal_entry_id},
        )


@pytest.mark.integration
def test_cannot_seal_journal_with_fewer_than_two_postings(
    journal_fixture: JournalFixture,
) -> None:
    _insert_posting(
        journal_fixture,
        line_number=0,
        ledger_account="suspense:unclassified",
        amount_cents=0,
    )

    with pytest.raises(IntegrityError, match="at least two postings"):
        _seal_journal(journal_fixture)


@pytest.mark.integration
def test_cannot_seal_unbalanced_journal(journal_fixture: JournalFixture) -> None:
    _insert_posting(
        journal_fixture,
        line_number=0,
        ledger_account="suspense:unclassified",
        amount_cents=1250,
    )
    _insert_posting(
        journal_fixture,
        line_number=1,
        ledger_account="financial:checking",
        amount_cents=-1200,
    )

    with pytest.raises(IntegrityError, match="must balance to zero"):
        _seal_journal(journal_fixture)


@pytest.mark.integration
def test_balanced_journal_can_be_sealed(journal_fixture: JournalFixture) -> None:
    _insert_posting(
        journal_fixture,
        line_number=0,
        ledger_account="suspense:unclassified",
        amount_cents=1250,
    )
    _insert_posting(
        journal_fixture,
        line_number=1,
        ledger_account="financial:checking",
        amount_cents=-1250,
    )

    _seal_journal(journal_fixture)

    with journal_fixture.engine.connect() as connection:
        sealed_at = connection.scalar(
            text(
                """
                SELECT sealed_at
                FROM journal_entries
                WHERE id = :journal_entry_id
                """
            ),
            {"journal_entry_id": journal_fixture.journal_entry_id},
        )

    assert sealed_at is not None


@pytest.mark.integration
def test_journal_cannot_be_inserted_already_sealed(
    journal_fixture: JournalFixture,
) -> None:
    with (
        pytest.raises(IntegrityError, match="cannot be inserted already sealed"),
        journal_fixture.engine.begin() as connection,
    ):
        connection.execute(
            text(
                """
                INSERT INTO journal_entries (
                    id,
                    external_transaction_id,
                    description,
                    sealed_at
                )
                VALUES (
                    :id,
                    :external_transaction_id,
                    'Invalid shortcut',
                    now()
                )
                """
            ),
            {
                "id": uuid4(),
                "external_transaction_id": journal_fixture.external_transaction_id,
            },
        )


@pytest.mark.integration
def test_sealed_journal_and_postings_are_immutable(
    journal_fixture: JournalFixture,
) -> None:
    _insert_posting(
        journal_fixture,
        line_number=0,
        ledger_account="suspense:unclassified",
        amount_cents=1250,
    )
    _insert_posting(
        journal_fixture,
        line_number=1,
        ledger_account="financial:checking",
        amount_cents=-1250,
    )
    _seal_journal(journal_fixture)

    statements = (
        "UPDATE journal_entries SET description = 'Changed' WHERE id = :id",
        "DELETE FROM journal_entries WHERE id = :id",
        "UPDATE postings SET amount_cents = 0 WHERE journal_entry_id = :id",
        "DELETE FROM postings WHERE journal_entry_id = :id",
        """
        INSERT INTO postings (
            journal_entry_id,
            line_number,
            ledger_account,
            amount_cents
        )
        VALUES (:id, 2, 'unexpected', 0)
        """,
    )

    for statement in statements:
        with (
            pytest.raises(IntegrityError, match="sealed journal"),
            journal_fixture.engine.begin() as connection,
        ):
            connection.execute(
                text(statement),
                {"id": journal_fixture.journal_entry_id},
            )
