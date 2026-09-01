from collections import defaultdict
from uuid import UUID

from domain.invariants import assert_balanced
from domain.ledger import (
    apply_transaction_added,
    apply_transaction_modified,
    apply_transaction_removed,
)
from domain.models import LedgerState, Transaction, TransactionRemoval

ACCOUNT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def test_transaction_feed_reconciles_to_one_correct_final_state() -> None:
    grocery = Transaction(
        account_id=ACCOUNT_ID,
        provider_transaction_id="grocery-1",
        amount_cents=1_250,
        description="Neighborhood Market",
    )
    refund = Transaction(
        account_id=ACCOUNT_ID,
        provider_transaction_id="refund-1",
        amount_cents=-500,
        description="Returned item",
    )
    grocery_with_tip = Transaction(
        account_id=ACCOUNT_ID,
        provider_transaction_id="grocery-1",
        amount_cents=1_400,
        description="Neighborhood Market with tip",
    )

    state = LedgerState.empty()
    state = apply_transaction_added(state, grocery, UUID(int=1))

    state_before_duplicate = state
    state = apply_transaction_added(state, grocery, UUID(int=99))
    assert state is state_before_duplicate

    state = apply_transaction_added(state, refund, UUID(int=2))
    state = apply_transaction_modified(
        state,
        grocery_with_tip,
        reversal_entry_id=UUID(int=3),
        replacement_entry_id=UUID(int=4),
    )
    refund_removal = TransactionRemoval(
        account_id=refund.account_id,
        provider_transaction_id=refund.provider_transaction_id,
    )
    state = apply_transaction_removed(state, refund_removal, UUID(int=5))

    state_before_duplicate_removal = state
    state = apply_transaction_removed(state, refund_removal, UUID(int=98))
    assert state is state_before_duplicate_removal

    assert state.transactions_by_provider_id == {
        "grocery-1": grocery_with_tip,
        "refund-1": refund,
    }
    assert state.removed_provider_transaction_ids == {"refund-1"}
    assert tuple(entry.journal_entry_id for entry in state.journal_entries) == (
        UUID(int=1),
        UUID(int=2),
        UUID(int=3),
        UUID(int=4),
        UUID(int=5),
    )

    (
        grocery_original,
        refund_original,
        grocery_reversal,
        grocery_current,
        refund_reversal,
    ) = state.journal_entries
    assert grocery_reversal.reversal_of_entry_id == grocery_original.journal_entry_id
    assert refund_reversal.reversal_of_entry_id == refund_original.journal_entry_id
    assert grocery_current.reversal_of_entry_id is None

    totals_by_account: defaultdict[str, int] = defaultdict(int)
    for entry in state.journal_entries:
        assert_balanced(entry.postings)
        for posting in entry.postings:
            totals_by_account[posting.ledger_account] += posting.amount_cents

    assert dict(totals_by_account) == {
        "suspense:unclassified": 1_400,
        f"financial:{ACCOUNT_ID}": -1_400,
    }
