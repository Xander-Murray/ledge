from uuid import UUID

import pytest

from domain.models import Transaction, TransactionRemoval
from providers.base import TransactionSyncPage

ACCOUNT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def test_sync_page_retains_immutable_change_tuples() -> None:
    transaction = Transaction(
        account_id=ACCOUNT_ID,
        provider_transaction_id="grocery-1",
        amount_cents=1_250,
        description="Neighborhood Market",
    )
    removal = TransactionRemoval(
        account_id=ACCOUNT_ID,
        provider_transaction_id="refund-1",
    )

    page = TransactionSyncPage(
        added=(transaction,),
        modified=(),
        removed=(removal,),
        next_cursor="cursor-1",
        has_more=True,
    )

    assert page.added == (transaction,)
    assert page.modified == ()
    assert page.removed == (removal,)


def test_sync_page_with_more_results_requires_a_cursor() -> None:
    with pytest.raises(ValueError, match="must provide a next cursor"):
        TransactionSyncPage(
            added=(),
            modified=(),
            removed=(),
            next_cursor="",
            has_more=True,
        )
