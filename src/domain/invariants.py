from collections.abc import Sequence
from typing import Protocol


class PostingAmount(Protocol):
    amount_cents: int


def assert_balanced(postings: Sequence[PostingAmount]) -> None:
    """Validate that a double-entry journal has balanced postings."""
    if len(postings) < 2:
        raise ValueError("A journal entry requires at least two postings")

    total = sum(posting.amount_cents for posting in postings)
    if total != 0:
        raise ValueError(f"Postings are not balanced: total is {total} cents")
