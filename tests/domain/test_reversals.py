from uuid import UUID

import pytest

from domain.ledger import create_reversal_entry
from domain.models import JournalEntry, Posting

ORIGINAL_ENTRY_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
REVERSAL_ENTRY_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def create_original_entry() -> JournalEntry:
    return JournalEntry(
        journal_entry_id=ORIGINAL_ENTRY_ID,
        source_provider_transaction_id="provider-transaction-1",
        description="Neighborhood Market",
        postings=(
            Posting(ledger_account="suspense:unclassified", amount_cents=1_250),
            Posting(ledger_account="financial:checking", amount_cents=-1_250),
        ),
    )


def test_reversal_negates_postings_and_links_to_the_original() -> None:
    original = create_original_entry()

    reversal = create_reversal_entry(original, REVERSAL_ENTRY_ID)

    assert reversal.journal_entry_id == REVERSAL_ENTRY_ID
    assert reversal.source_provider_transaction_id == "provider-transaction-1"
    assert reversal.reversal_of_entry_id == ORIGINAL_ENTRY_ID
    assert reversal.postings == (
        Posting(ledger_account="suspense:unclassified", amount_cents=-1_250),
        Posting(ledger_account="financial:checking", amount_cents=1_250),
    )


def test_reversal_negates_every_posting_without_changing_the_original() -> None:
    original = JournalEntry(
        journal_entry_id=ORIGINAL_ENTRY_ID,
        source_provider_transaction_id="provider-transaction-1",
        description="Purchase with fee",
        postings=(
            Posting(ledger_account="suspense:purchase", amount_cents=900),
            Posting(ledger_account="suspense:fee", amount_cents=100),
            Posting(ledger_account="financial:checking", amount_cents=-1_000),
        ),
    )
    original_postings = original.postings

    reversal = create_reversal_entry(original, REVERSAL_ENTRY_ID)

    assert reversal.postings == (
        Posting(ledger_account="suspense:purchase", amount_cents=-900),
        Posting(ledger_account="suspense:fee", amount_cents=-100),
        Posting(ledger_account="financial:checking", amount_cents=1_000),
    )
    assert original.postings == original_postings


def test_reversal_requires_a_new_journal_entry_id() -> None:
    original = create_original_entry()

    with pytest.raises(ValueError, match="must differ from the original"):
        create_reversal_entry(original, ORIGINAL_ENTRY_ID)
