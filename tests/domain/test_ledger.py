from uuid import UUID

import pytest

from domain.ledger import (
    TransactionConflictError,
    apply_transaction_added,
    assert_balanced,
    create_journal_entry,
    create_transaction_postings,
)
from domain.models import JournalEntry, LedgerState, Posting, Transaction


def test_normal_debit_creates_balanced_postings() -> None:
    transaction = Transaction(
        account_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        provider_transaction_id="provider-transaction-1",
        amount_cents=1_250,
        description="Neighborhood Market",
    )

    postings = create_transaction_postings(transaction)

    assert postings == (
        Posting(ledger_account="suspense:unclassified", amount_cents=1_250),
        Posting(
            ledger_account="financial:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            amount_cents=-1_250,
        ),
    )
    assert_balanced(postings)


def test_refund_reverses_the_posting_directions() -> None:
    transaction = Transaction(
        account_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        provider_transaction_id="provider-refund-1",
        amount_cents=-500,
        description="Neighborhood Market refund",
    )

    postings = create_transaction_postings(transaction)

    assert postings == (
        Posting(ledger_account="suspense:unclassified", amount_cents=-500),
        Posting(
            ledger_account="financial:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            amount_cents=500,
        ),
    )
    assert_balanced(postings)


def test_transaction_creates_an_initial_journal_entry() -> None:
    transaction = Transaction(
        account_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        provider_transaction_id="provider-transaction-1",
        amount_cents=1_250,
        description="Neighborhood Market",
    )
    journal_entry_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

    entry = create_journal_entry(transaction, journal_entry_id)

    assert entry == JournalEntry(
        journal_entry_id=journal_entry_id,
        source_provider_transaction_id="provider-transaction-1",
        description="Neighborhood Market",
        postings=(
            Posting(ledger_account="suspense:unclassified", amount_cents=1_250),
            Posting(
                ledger_account="financial:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                amount_cents=-1_250,
            ),
        ),
    )


def test_new_transaction_is_added_to_a_new_ledger_state() -> None:
    transaction = Transaction(
        account_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        provider_transaction_id="provider-transaction-1",
        amount_cents=1_250,
        description="Neighborhood Market",
    )
    journal_entry_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

    result = apply_transaction_added(
        LedgerState.empty(),
        transaction,
        journal_entry_id,
    )

    assert result.transactions_by_provider_id == {
        transaction.provider_transaction_id: transaction,
    }
    assert len(result.journal_entries) == 1
    assert result.journal_entries[0].journal_entry_id == journal_entry_id


def test_identical_duplicate_returns_the_existing_state() -> None:
    transaction = Transaction(
        account_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        provider_transaction_id="provider-transaction-1",
        amount_cents=1_250,
        description="Neighborhood Market",
    )
    state = apply_transaction_added(
        LedgerState.empty(),
        transaction,
        UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
    )

    result = apply_transaction_added(
        state,
        transaction,
        UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
    )

    assert result is state
    assert len(result.journal_entries) == 1


def test_conflicting_added_transaction_is_rejected() -> None:
    original = Transaction(
        account_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
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
    state = apply_transaction_added(
        LedgerState.empty(),
        original,
        UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
    )

    with pytest.raises(
        TransactionConflictError,
        match="already exists with different data",
    ):
        apply_transaction_added(
            state,
            conflicting,
            UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
        )

    assert (
        state.transactions_by_provider_id[original.provider_transaction_id] == original
    )
    assert len(state.journal_entries) == 1


def test_adding_transaction_does_not_mutate_the_input_state() -> None:
    initial_state = LedgerState.empty()
    transaction = Transaction(
        account_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        provider_transaction_id="provider-transaction-1",
        amount_cents=1_250,
        description="Neighborhood Market",
    )

    result = apply_transaction_added(
        initial_state,
        transaction,
        UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
    )

    assert result is not initial_state
    assert initial_state.transactions_by_provider_id == {}
    assert initial_state.journal_entries == ()


def test_unbalanced_journal_with_more_than_two_postings_is_rejected() -> None:
    postings = (
        Posting(ledger_account="expense:groceries", amount_cents=1_000),
        Posting(ledger_account="financial:checking", amount_cents=-1_000),
        Posting(ledger_account="expense:fee", amount_cents=1),
    )

    with pytest.raises(ValueError, match="Postings are not balanced"):
        assert_balanced(postings)


def test_balanced_journal_can_have_more_than_two_postings() -> None:
    postings = (
        Posting(ledger_account="expense:groceries", amount_cents=900),
        Posting(ledger_account="expense:fee", amount_cents=100),
        Posting(ledger_account="financial:checking", amount_cents=-1_000),
    )

    assert_balanced(postings)


@pytest.mark.parametrize(
    "postings",
    [
        (),
        (Posting(ledger_account="financial:checking", amount_cents=0),),
    ],
)
def test_journal_requires_at_least_two_postings(
    postings: tuple[Posting, ...],
) -> None:
    with pytest.raises(ValueError, match="at least two postings"):
        assert_balanced(postings)


@pytest.mark.parametrize("amount_cents", [12.5, True])
def test_posting_amount_must_be_integer_cents(amount_cents: object) -> None:
    with pytest.raises(TypeError, match="must be an integer"):
        Posting(
            ledger_account="financial:checking",
            amount_cents=amount_cents,  # type: ignore[arg-type]
        )
