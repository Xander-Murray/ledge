from uuid import UUID

from domain.invariants import assert_balanced
from domain.models import JournalEntry, LedgerState, Posting, Transaction


class TransactionConflictError(ValueError):
    """Raised when an added event conflicts with a known transaction."""


class TransactionNotFoundError(LookupError):
    """Raised when a transition targets an unknown provider transaction."""


class TransactionStateError(ValueError):
    """Raised when a transition is invalid for the transaction's current state."""


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


def create_reversal_entry(
    original_entry: JournalEntry,
    reversal_entry_id: UUID,
) -> JournalEntry:
    """Create a reversal journal entry for a provider transaction."""
    if reversal_entry_id == original_entry.journal_entry_id:
        raise ValueError("Reversal entry ID must differ from the original entry ID")
    reversed_postings = tuple(
        Posting(
            ledger_account=posting.ledger_account,
            amount_cents=-posting.amount_cents,
        )
        for posting in original_entry.postings
    )
    assert_balanced(reversed_postings)
    return JournalEntry(
        journal_entry_id=reversal_entry_id,
        source_provider_transaction_id=original_entry.source_provider_transaction_id,
        description=f"Reversal of {original_entry.description}",
        postings=reversed_postings,
        reversal_of_entry_id=original_entry.journal_entry_id,
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
            removed_provider_transaction_ids=state.removed_provider_transaction_ids,
            superseded_by_provider_id=state.superseded_by_provider_id,
        )

    if existing_transaction == transaction:
        return state

    raise TransactionConflictError(
        f"Transaction {provider_id!r} already exists with different data"
    )


def apply_transaction_removed(
    state: LedgerState,
    transaction: Transaction,
    journal_entry_id: UUID,
) -> LedgerState:
    """Reverse and mark a known provider transaction as removed."""
    provider_id = transaction.provider_transaction_id
    existing_transaction = state.transactions_by_provider_id.get(provider_id)

    if existing_transaction is None:
        raise TransactionNotFoundError(f"Transaction {provider_id!r} does not exist")
    if existing_transaction != transaction:
        raise TransactionConflictError(
            f"Transaction {provider_id!r} removal data differs from current data"
        )
    if provider_id in state.removed_provider_transaction_ids:
        return state

    active_entry = _find_active_journal_entry(state, provider_id)
    reversal_entry = create_reversal_entry(active_entry, journal_entry_id)

    return LedgerState(
        transactions_by_provider_id=state.transactions_by_provider_id,
        journal_entries=state.journal_entries + (reversal_entry,),
        removed_provider_transaction_ids=(
            state.removed_provider_transaction_ids | {provider_id}
        ),
        superseded_by_provider_id=state.superseded_by_provider_id,
    )


def apply_transaction_modified(
    state: LedgerState,
    transaction: Transaction,
    reversal_entry_id: UUID,
    replacement_entry_id: UUID,
) -> LedgerState:
    """Reverse a known transaction's active effect and apply its new data."""
    provider_id = transaction.provider_transaction_id
    existing_transaction = state.transactions_by_provider_id.get(provider_id)

    if existing_transaction is None:
        raise TransactionNotFoundError(f"Transaction {provider_id!r} does not exist")
    if provider_id in state.removed_provider_transaction_ids:
        raise TransactionStateError(
            f"Removed transaction {provider_id!r} cannot be modified"
        )
    if existing_transaction == transaction:
        return state

    active_entry = _find_active_journal_entry(state, provider_id)
    reversal_entry = create_reversal_entry(active_entry, reversal_entry_id)
    replacement_entry = create_journal_entry(transaction, replacement_entry_id)
    transactions = dict(state.transactions_by_provider_id)
    transactions[provider_id] = transaction

    return LedgerState(
        transactions_by_provider_id=transactions,
        journal_entries=state.journal_entries
        + (
            reversal_entry,
            replacement_entry,
        ),
        removed_provider_transaction_ids=state.removed_provider_transaction_ids,
        superseded_by_provider_id=state.superseded_by_provider_id,
    )


def apply_pending_replacement(
    state: LedgerState,
    posted_transaction: Transaction,
    reversal_entry_id: UUID,
    posted_entry_id: UUID,
) -> LedgerState:
    """Replace a pending transaction with its linked posted transaction."""
    if posted_transaction.pending:
        raise TransactionStateError("A pending transaction cannot be a replacement")

    pending_id = posted_transaction.pending_transaction_id
    if pending_id is None:
        raise TransactionStateError(
            "A posted replacement must reference a pending transaction"
        )

    pending_transaction = state.transactions_by_provider_id.get(pending_id)
    if pending_transaction is None:
        raise TransactionNotFoundError(
            f"Pending transaction {pending_id!r} does not exist"
        )
    if not pending_transaction.pending:
        raise TransactionStateError(
            f"Transaction {pending_id!r} is not pending and cannot be replaced"
        )
    if pending_transaction.account_id != posted_transaction.account_id:
        raise TransactionConflictError(
            "Pending and posted transactions must belong to the same account"
        )

    posted_id = posted_transaction.provider_transaction_id
    existing_replacement_id = state.superseded_by_provider_id.get(pending_id)
    existing_posted_transaction = state.transactions_by_provider_id.get(posted_id)

    if existing_replacement_id is not None:
        if (
            existing_replacement_id == posted_id
            and existing_posted_transaction == posted_transaction
        ):
            return state
        raise TransactionStateError(
            f"Pending transaction {pending_id!r} was already superseded"
        )
    if existing_posted_transaction is not None:
        raise TransactionConflictError(
            f"Posted transaction {posted_id!r} already exists without this replacement"
        )
    if reversal_entry_id == posted_entry_id:
        raise ValueError("Reversal and posted entry IDs must differ")

    new_entries: tuple[JournalEntry, ...] = ()
    if pending_id not in state.removed_provider_transaction_ids:
        active_entry = _find_active_journal_entry(state, pending_id)
        new_entries += (create_reversal_entry(active_entry, reversal_entry_id),)
    new_entries += (create_journal_entry(posted_transaction, posted_entry_id),)

    transactions = dict(state.transactions_by_provider_id)
    transactions[posted_id] = posted_transaction
    superseded_by_provider_id = dict(state.superseded_by_provider_id)
    superseded_by_provider_id[pending_id] = posted_id

    return LedgerState(
        transactions_by_provider_id=transactions,
        journal_entries=state.journal_entries + new_entries,
        removed_provider_transaction_ids=(
            state.removed_provider_transaction_ids | {pending_id}
        ),
        superseded_by_provider_id=superseded_by_provider_id,
    )


def _find_active_journal_entry(
    state: LedgerState,
    provider_transaction_id: str,
) -> JournalEntry:
    reversed_entry_ids = {
        entry.reversal_of_entry_id
        for entry in state.journal_entries
        if entry.reversal_of_entry_id is not None
    }
    for entry in reversed(state.journal_entries):
        if (
            entry.source_provider_transaction_id == provider_transaction_id
            and entry.journal_entry_id not in reversed_entry_ids
            and entry.reversal_of_entry_id is None
        ):
            return entry

    raise TransactionStateError(
        f"Transaction {provider_transaction_id!r} has no active journal entry"
    )
