from uuid import UUID

import pytest

from domain.models import (
    JournalEntry,
    LedgerState,
    Posting,
    Transaction,
    TransactionRemoval,
)


def test_transaction_removal_rejects_empty_provider_id() -> None:
    with pytest.raises(ValueError, match="provider_transaction_id must not be empty"):
        TransactionRemoval(
            account_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            provider_transaction_id="  ",
        )


def test_journal_entry_accepts_balanced_postings() -> None:
    entry = JournalEntry(
        journal_entry_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        source_provider_transaction_id="provider-transaction-1",
        description="Neighborhood Market",
        postings=(
            Posting(ledger_account="suspense:unclassified", amount_cents=1_250),
            Posting(ledger_account="financial:checking", amount_cents=-1_250),
        ),
    )

    assert entry.reversal_of_entry_id is None


def test_journal_entry_rejects_unbalanced_postings() -> None:
    with pytest.raises(ValueError, match="Postings are not balanced"):
        JournalEntry(
            journal_entry_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
            source_provider_transaction_id="provider-transaction-1",
            description="Neighborhood Market",
            postings=(
                Posting(
                    ledger_account="suspense:unclassified",
                    amount_cents=1_250,
                ),
                Posting(ledger_account="financial:checking", amount_cents=-1_249),
            ),
        )


def test_empty_ledger_state_contains_no_transactions_or_entries() -> None:
    state = LedgerState.empty()

    assert dict(state.transactions_by_provider_id) == {}
    assert state.journal_entries == ()


def test_ledger_state_copies_the_transaction_mapping() -> None:
    transaction = Transaction(
        account_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        provider_transaction_id="provider-transaction-1",
        amount_cents=1_250,
        description="Neighborhood Market",
    )
    transactions = {transaction.provider_transaction_id: transaction}

    state = LedgerState(
        transactions_by_provider_id=transactions,
        journal_entries=(),
    )
    transactions.clear()

    assert state.transactions_by_provider_id == {
        "provider-transaction-1": transaction,
    }
