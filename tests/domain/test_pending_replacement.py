from uuid import UUID

import pytest

from domain.ledger import (
    TransactionConflictError,
    TransactionNotFoundError,
    TransactionStateError,
    apply_pending_transaction_posted,
    apply_transaction_added,
    apply_transaction_removed,
)
from domain.models import LedgerState, Transaction, TransactionRemoval

ACCOUNT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
OTHER_ACCOUNT_ID = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
PENDING_ENTRY_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
REVERSAL_ENTRY_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
POSTED_ENTRY_ID = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")


def pending_transaction() -> Transaction:
    return Transaction(
        account_id=ACCOUNT_ID,
        provider_transaction_id="pending-restaurant",
        amount_cents=2_000,
        description="Corner Cafe",
        is_pending=True,
    )


def posted_transaction(*, account_id: UUID = ACCOUNT_ID) -> Transaction:
    return Transaction(
        account_id=account_id,
        provider_transaction_id="posted-restaurant",
        amount_cents=2_300,
        description="Corner Cafe with tip",
        pending_provider_transaction_id="pending-restaurant",
    )


def state_with_pending() -> LedgerState:
    return apply_transaction_added(
        LedgerState.empty(),
        pending_transaction(),
        PENDING_ENTRY_ID,
    )


def test_pending_transaction_is_reversed_and_replaced_by_posted_transaction() -> None:
    state = state_with_pending()
    posted = posted_transaction()

    result = apply_pending_transaction_posted(
        state,
        posted,
        REVERSAL_ENTRY_ID,
        POSTED_ENTRY_ID,
    )

    assert set(result.transactions_by_provider_id) == {
        "pending-restaurant",
        "posted-restaurant",
    }
    assert result.transactions_by_provider_id["posted-restaurant"] == posted
    assert result.replaced_provider_transaction_ids == {"pending-restaurant"}
    assert tuple(entry.journal_entry_id for entry in result.journal_entries) == (
        PENDING_ENTRY_ID,
        REVERSAL_ENTRY_ID,
        POSTED_ENTRY_ID,
    )
    assert result.journal_entries[1].reversal_of_entry_id == PENDING_ENTRY_ID
    assert (
        sum(
            posting.amount_cents
            for entry in result.journal_entries
            for posting in entry.postings
            if posting.ledger_account == "suspense:unclassified"
        )
        == 2_300
    )


def test_identical_pending_replacement_is_idempotent() -> None:
    posted = posted_transaction()
    replaced = apply_pending_transaction_posted(
        state_with_pending(),
        posted,
        REVERSAL_ENTRY_ID,
        POSTED_ENTRY_ID,
    )

    result = apply_pending_transaction_posted(
        replaced,
        posted,
        UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
        UUID("11111111-1111-1111-1111-111111111111"),
    )

    assert result is replaced
    assert len(result.journal_entries) == 3


def test_provider_removal_for_replaced_pending_transaction_is_a_no_op() -> None:
    replaced = apply_pending_transaction_posted(
        state_with_pending(),
        posted_transaction(),
        REVERSAL_ENTRY_ID,
        POSTED_ENTRY_ID,
    )

    result = apply_transaction_removed(
        replaced,
        TransactionRemoval(
            account_id=ACCOUNT_ID,
            provider_transaction_id="pending-restaurant",
        ),
        UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
    )

    assert result is replaced


def test_pending_replacement_rejects_unknown_or_cross_account_source() -> None:
    with pytest.raises(TransactionNotFoundError, match="does not exist"):
        apply_pending_transaction_posted(
            LedgerState.empty(),
            posted_transaction(),
            REVERSAL_ENTRY_ID,
            POSTED_ENTRY_ID,
        )

    with pytest.raises(TransactionConflictError, match="different account"):
        apply_pending_transaction_posted(
            state_with_pending(),
            posted_transaction(account_id=OTHER_ACCOUNT_ID),
            REVERSAL_ENTRY_ID,
            POSTED_ENTRY_ID,
        )


def test_ordinary_addition_rejects_posted_replacement() -> None:
    with pytest.raises(TransactionStateError, match="pending-to-posted"):
        apply_transaction_added(
            LedgerState.empty(),
            posted_transaction(),
            POSTED_ENTRY_ID,
        )
