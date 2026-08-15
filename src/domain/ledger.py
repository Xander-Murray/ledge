from collections.abc import Sequence

from domain.models import Posting, Transaction


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


def assert_balanced(postings: Sequence[Posting]) -> None:
    """Validate that a double-entry journal has balanced postings."""
    if len(postings) < 2:
        raise ValueError("A journal entry requires at least two postings")

    total = sum(posting.amount_cents for posting in postings)
    if total != 0:
        raise ValueError(f"Postings are not balanced: total is {total} cents")
