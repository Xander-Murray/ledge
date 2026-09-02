from pathlib import Path

import pytest

from providers.fake import FakeTransactionProvider, UnknownProviderCursorError

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "providers"
    / "transaction_sync_pages.json"
)


def test_fake_provider_returns_normalized_paginated_changes() -> None:
    provider = FakeTransactionProvider.from_json(FIXTURE_PATH)

    first_page = provider.fetch_transaction_updates(None)
    second_page = provider.fetch_transaction_updates(first_page.next_cursor)

    assert [item.provider_transaction_id for item in first_page.added] == [
        "grocery-1",
        "refund-1",
    ]
    assert first_page.modified == ()
    assert first_page.removed == ()
    assert first_page.next_cursor == "cursor-1"
    assert first_page.has_more is True

    assert [item.provider_transaction_id for item in second_page.modified] == [
        "grocery-1"
    ]
    assert second_page.modified[0].amount_cents == 1_400
    assert [item.provider_transaction_id for item in second_page.removed] == [
        "refund-1"
    ]
    assert second_page.next_cursor == "cursor-2"
    assert second_page.has_more is False
    assert provider.requested_cursors == [None, "cursor-1"]


def test_fake_provider_returns_empty_page_at_current_cursor() -> None:
    provider = FakeTransactionProvider.from_json(FIXTURE_PATH)

    page = provider.fetch_transaction_updates("cursor-2")

    assert page.added == ()
    assert page.modified == ()
    assert page.removed == ()
    assert page.next_cursor == "cursor-2"
    assert page.has_more is False


def test_fake_provider_parses_pending_replacement_fields() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "providers"
        / "pending_transaction_sync_pages.json"
    )
    provider = FakeTransactionProvider.from_json(fixture_path)

    pending = provider.fetch_transaction_updates(None).added[0]
    posted = provider.fetch_transaction_updates("pending-cursor").added[0]

    assert pending.is_pending is True
    assert pending.pending_provider_transaction_id is None
    assert posted.is_pending is False
    assert posted.pending_provider_transaction_id == "pending-restaurant"


def test_fake_provider_rejects_unknown_cursor() -> None:
    provider = FakeTransactionProvider.from_json(FIXTURE_PATH)

    with pytest.raises(UnknownProviderCursorError, match="unknown-cursor"):
        provider.fetch_transaction_updates("unknown-cursor")
