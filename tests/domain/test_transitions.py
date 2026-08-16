from uuid import UUID

import pytest

from domain.ledger import (
    TransactionConflictError,
    TransactionNotFoundError,
    TransactionStateError,
    apply_transaction_added,
    apply_transaction_modified,
    apply_transaction_removed,
)
from domain.models import LedgerState, Transaction

ACCOUNT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
ORIGINAL_ENTRY_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
REVERSAL_ENTRY_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
REPLACEMENT_ENTRY_ID = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")


def create_transaction(
    *,
    amount_cents: int = 1_250,
    description: str = "Neighborhood Market",
) -> Transaction:
    return Transaction(
        account_id=ACCOUNT_ID,
        provider_transaction_id="provider-transaction-1",
        amount_cents=amount_cents,
        description=description,
    )


def create_state_with_transaction(
    transaction: Transaction | None = None,
) -> LedgerState:
    return apply_transaction_added(
        LedgerState.empty(),
        transaction or create_transaction(),
        ORIGINAL_ENTRY_ID,
    )


def test_removed_transaction_is_reversed_and_retained_for_audit() -> None:
    transaction = create_transaction()
    state = create_state_with_transaction(transaction)

    result = apply_transaction_removed(state, transaction, REVERSAL_ENTRY_ID)

    assert result.transactions_by_provider_id == {
        transaction.provider_transaction_id: transaction,
    }
    assert result.removed_provider_transaction_ids == {
        transaction.provider_transaction_id,
    }
    assert len(result.journal_entries) == 2
    original_entry, reversal_entry = result.journal_entries
    assert reversal_entry.reversal_of_entry_id == original_entry.journal_entry_id
    assert tuple(posting.amount_cents for posting in reversal_entry.postings) == (
        -1_250,
        1_250,
    )
    assert state.removed_provider_transaction_ids == frozenset()
    assert len(state.journal_entries) == 1


def test_duplicate_removal_returns_the_existing_state() -> None:
    transaction = create_transaction()
    removed_state = apply_transaction_removed(
        create_state_with_transaction(transaction),
        transaction,
        REVERSAL_ENTRY_ID,
    )

    result = apply_transaction_removed(
        removed_state,
        transaction,
        REPLACEMENT_ENTRY_ID,
    )

    assert result is removed_state
    assert len(result.journal_entries) == 2


def test_removing_an_unknown_transaction_is_rejected() -> None:
    with pytest.raises(TransactionNotFoundError, match="does not exist"):
        apply_transaction_removed(
            LedgerState.empty(),
            create_transaction(),
            REVERSAL_ENTRY_ID,
        )


def test_removal_data_must_match_the_current_transaction() -> None:
    current = create_transaction()
    conflicting = create_transaction(amount_cents=1_400)

    with pytest.raises(TransactionConflictError, match="differs from current data"):
        apply_transaction_removed(
            create_state_with_transaction(current),
            conflicting,
            REVERSAL_ENTRY_ID,
        )


def test_modified_transaction_reverses_old_effect_and_applies_replacement() -> None:
    original = create_transaction()
    modified = create_transaction(
        amount_cents=1_400,
        description="Neighborhood Market with tip",
    )
    state = create_state_with_transaction(original)

    result = apply_transaction_modified(
        state,
        modified,
        REVERSAL_ENTRY_ID,
        REPLACEMENT_ENTRY_ID,
    )

    assert result.transactions_by_provider_id == {
        modified.provider_transaction_id: modified,
    }
    assert tuple(entry.journal_entry_id for entry in result.journal_entries) == (
        ORIGINAL_ENTRY_ID,
        REVERSAL_ENTRY_ID,
        REPLACEMENT_ENTRY_ID,
    )
    original_entry, reversal_entry, replacement_entry = result.journal_entries
    assert reversal_entry.reversal_of_entry_id == original_entry.journal_entry_id
    assert replacement_entry.reversal_of_entry_id is None
    assert tuple(posting.amount_cents for posting in replacement_entry.postings) == (
        1_400,
        -1_400,
    )
    assert (
        state.transactions_by_provider_id[original.provider_transaction_id] == original
    )
    assert len(state.journal_entries) == 1


def test_duplicate_modification_returns_the_existing_state() -> None:
    modified = create_transaction(amount_cents=1_400)
    modified_state = apply_transaction_modified(
        create_state_with_transaction(),
        modified,
        REVERSAL_ENTRY_ID,
        REPLACEMENT_ENTRY_ID,
    )

    result = apply_transaction_modified(
        modified_state,
        modified,
        UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"),
        UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
    )

    assert result is modified_state
    assert len(result.journal_entries) == 3


def test_modifying_an_unknown_transaction_is_rejected() -> None:
    with pytest.raises(TransactionNotFoundError, match="does not exist"):
        apply_transaction_modified(
            LedgerState.empty(),
            create_transaction(),
            REVERSAL_ENTRY_ID,
            REPLACEMENT_ENTRY_ID,
        )


def test_removed_transaction_cannot_be_modified() -> None:
    original = create_transaction()
    removed_state = apply_transaction_removed(
        create_state_with_transaction(original),
        original,
        REVERSAL_ENTRY_ID,
    )

    with pytest.raises(TransactionStateError, match="cannot be modified"):
        apply_transaction_modified(
            removed_state,
            create_transaction(amount_cents=1_400),
            REPLACEMENT_ENTRY_ID,
            UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"),
        )
