from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    SmallInteger,
    UniqueConstraint,
    inspect,
)

from persistence.models import (
    Base,
    ExternalTransactionModel,
    JournalEntryModel,
    PostingModel,
)


def test_journal_and_posting_tables_have_their_expected_primary_keys() -> None:
    assert {"journal_entries", "postings"} <= set(Base.metadata.tables)
    assert tuple(JournalEntryModel.__table__.primary_key.columns) == (
        JournalEntryModel.__table__.c.id,
    )
    assert tuple(PostingModel.__table__.primary_key.columns) == (
        PostingModel.__table__.c.id,
    )
    assert PostingModel.__table__.c.id.identity is not None


def test_journal_entry_has_required_fields() -> None:
    columns = JournalEntryModel.__table__.c

    assert set(columns.keys()) == {
        "id",
        "external_transaction_id",
        "description",
        "reversal_of_entry_id",
        "created_at",
        "sealed_at",
    }
    assert columns.external_transaction_id.nullable is False
    assert columns.description.nullable is False
    assert columns.reversal_of_entry_id.nullable is True
    assert columns.sealed_at.nullable is True


def test_journal_entry_timestamps_have_intentional_defaults() -> None:
    columns = JournalEntryModel.__table__.c

    assert isinstance(columns.created_at.type, DateTime)
    assert columns.created_at.type.timezone is True
    assert columns.created_at.nullable is False
    assert columns.created_at.server_default is not None
    assert isinstance(columns.sealed_at.type, DateTime)
    assert columns.sealed_at.type.timezone is True
    assert columns.sealed_at.server_default is None


def test_journal_entry_enforces_transaction_and_reversal_links() -> None:
    table = JournalEntryModel.__table__
    foreign_keys = {
        tuple(element.target_fullname for element in constraint.elements): constraint
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    unique_constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    transaction_fk = foreign_keys[("external_transactions.id",)]
    assert transaction_fk.ondelete == "RESTRICT"

    reversal_fk = foreign_keys[
        ("journal_entries.id", "journal_entries.external_transaction_id")
    ]
    assert reversal_fk.ondelete == "RESTRICT"

    assert unique_constraints["uq_journal_entries_identity"] == (
        "id",
        "external_transaction_id",
    )
    assert unique_constraints["uq_journal_entries_reversal"] == (
        "reversal_of_entry_id",
    )
    assert set(checks) == {"ck_journal_entries_not_self_reversal"}
    self_reversal_check = checks["ck_journal_entries_not_self_reversal"].lower()
    assert "reversal_of_entry_id is null" in self_reversal_check
    assert "reversal_of_entry_id <> id" in self_reversal_check


def test_posting_has_required_fields_and_types() -> None:
    columns = PostingModel.__table__.c

    assert set(columns.keys()) == {
        "id",
        "journal_entry_id",
        "line_number",
        "ledger_account",
        "amount_cents",
    }
    assert isinstance(columns.id.type, BigInteger)
    assert isinstance(columns.line_number.type, SmallInteger)
    assert isinstance(columns.amount_cents.type, BigInteger)
    for column in columns:
        assert column.nullable is False


def test_posting_enforces_journal_order_and_valid_values() -> None:
    table = PostingModel.__table__
    journal_foreign_keys = tuple(table.c.journal_entry_id.foreign_keys)
    unique_constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert len(journal_foreign_keys) == 1
    assert journal_foreign_keys[0].target_fullname == "journal_entries.id"
    assert journal_foreign_keys[0].ondelete == "RESTRICT"
    assert unique_constraints["uq_postings_journal_line"] == (
        "journal_entry_id",
        "line_number",
    )
    assert set(checks) == {
        "ck_postings_ledger_account_nonempty",
        "ck_postings_line_number_nonnegative",
    }
    assert ">= 0" in checks["ck_postings_line_number_nonnegative"]
    assert "trim(ledger_account)" in checks["ck_postings_ledger_account_nonempty"]


def test_journal_relationships_are_bidirectional() -> None:
    transaction_journals = inspect(ExternalTransactionModel).relationships[
        "journal_entries"
    ]
    journal_transaction = inspect(JournalEntryModel).relationships[
        "external_transaction"
    ]
    journal_postings = inspect(JournalEntryModel).relationships["postings"]
    posting_journal = inspect(PostingModel).relationships["journal_entry"]

    assert transaction_journals.mapper.class_ is JournalEntryModel
    assert transaction_journals.back_populates == "external_transaction"
    assert transaction_journals.uselist is True
    assert journal_transaction.mapper.class_ is ExternalTransactionModel
    assert journal_transaction.back_populates == "journal_entries"
    assert journal_transaction.uselist is False
    assert journal_postings.mapper.class_ is PostingModel
    assert journal_postings.back_populates == "journal_entry"
    assert journal_postings.uselist is True
    assert posting_journal.mapper.class_ is JournalEntryModel
    assert posting_journal.back_populates == "postings"
    assert posting_journal.uselist is False
