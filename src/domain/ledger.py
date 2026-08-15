from uuid import UUID

from domain.invariants import assert_balanced
from domain.models import JournalEntry, LedgerState, Posting, Transaction


class TransactionConflictError(ValueError):
    """Raised when an added event conflicts with a known transaction."""


def create_transaction_postings(transaction: Transaction) -> tuple[Posting, ...]:
    """Translate one provider transaction into balanced ledger postings."""
    postings = (
        Posting(
            ledger_account="suspense:unclassified",
            amount_cents=transaction.amount_cents,
        ),
        Posting(
            ledger_account=f"financial:{transaction.account_id}",
            amount_cents=-transaction.amount_cents,
        ),
    )
    assert_balanced(postings)
    return postings


def create_journal_entry(
    transaction: Transaction,
    journal_entry_id: UUID,
) -> JournalEntry:
    """Create an initial journal entry for a provider transaction."""
    return JournalEntry(
        journal_entry_id=journal_entry_id,
        source_provider_transaction_id=transaction.provider_transaction_id,
        description=transaction.description,
        postings=create_transaction_postings(transaction),
    )


def apply_transaction_added(
    state: LedgerState,
    transaction: Transaction,
    journal_entry_id: UUID,
) -> LedgerState:
    """Apply a new provider transaction to the ledger state."""
    provider_id = transaction.provider_transaction_id
    existing_transaction = state.transactions_by_provider_id.get(provider_id)

    if existing_transaction is None:
        transactions = dict(state.transactions_by_provider_id)
        transactions[provider_id] = transaction
        journal_entry = create_journal_entry(transaction, journal_entry_id)

        return LedgerState(
            transactions_by_provider_id=transactions,
            journal_entries=state.journal_entries + (journal_entry,),
        )

    if existing_transaction == transaction:
        return state

    raise TransactionConflictError(
        f"Transaction {provider_id!r} already exists with different data"
    )
