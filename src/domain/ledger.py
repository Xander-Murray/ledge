from domain.models import Posting, Transaction


def verify_integer_cents(object: Posting | object: Transaction) -> None:
    """Raise an exception if the posting amount is not an interger."""
    if not isinstance(object.amount_cents, int):
        raise ValueError("Posting amount must be an integer")

def create_transaction_postings(transaction: Transaction) -> tuple[Posting, ...]:
    """Translate one provider transaction into balanced ledger postings."""

    verify_integer_cents(object.amount_cents)

    return (
        Posting(
            ledger_account="suspense:unclassified",
            amount_cents=transaction.amount_cents,
        ),
        Posting(
            ledger_account=f"financial:{transaction.account_id}",
            amount_cents=-transaction.amount_cents,
        ),
    )


def assert_balanced(postings: tuple[Posting, ...]) -> None:
    """Raise an exception if the postings are not balanced."""
    if not postings or len(postings) == 1:
        raise Exception("Not enought postings")

    total = sum(posting.amount_cents for posting in postings)

    if total != 0:
        raise ValueError("Postings are not balanced")
