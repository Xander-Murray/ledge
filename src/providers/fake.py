from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import UUID

from domain.models import Transaction, TransactionRemoval
from providers.base import TransactionSyncPage


class UnknownProviderCursorError(LookupError):
    """Raised when a fake provider receives a cursor absent from its fixture."""


class FakeTransactionProvider:
    """Return deterministic normalized pages without network access."""

    def __init__(
        self,
        pages_by_cursor: Mapping[str | None, TransactionSyncPage],
    ) -> None:
        self._pages_by_cursor = MappingProxyType(dict(pages_by_cursor))
        self.requested_cursors: list[str | None] = []

    @classmethod
    def from_json(cls, fixture_path: str | Path) -> FakeTransactionProvider:
        """Build a fake provider from a normalized transaction fixture."""
        raw_pages: Any = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
        if not isinstance(raw_pages, list):
            raise ValueError("provider fixture must contain a list of pages")

        pages_by_cursor: dict[str | None, TransactionSyncPage] = {}
        for raw_page in raw_pages:
            if not isinstance(raw_page, dict):
                raise ValueError("each provider fixture page must be an object")

            request_cursor = raw_page.get("request_cursor")
            if request_cursor is not None and not isinstance(request_cursor, str):
                raise ValueError("request_cursor must be a string or null")
            if request_cursor in pages_by_cursor:
                raise ValueError(f"duplicate request cursor {request_cursor!r}")

            pages_by_cursor[request_cursor] = TransactionSyncPage(
                added=tuple(
                    _parse_transaction(item) for item in raw_page.get("added", [])
                ),
                modified=tuple(
                    _parse_transaction(item) for item in raw_page.get("modified", [])
                ),
                removed=tuple(
                    _parse_removal(item) for item in raw_page.get("removed", [])
                ),
                next_cursor=raw_page["next_cursor"],
                has_more=raw_page["has_more"],
            )

        return cls(pages_by_cursor)

    def fetch_transaction_updates(
        self,
        cursor: str | None,
    ) -> TransactionSyncPage:
        self.requested_cursors.append(cursor)
        try:
            return self._pages_by_cursor[cursor]
        except KeyError as error:
            raise UnknownProviderCursorError(
                f"No fake provider page exists for cursor {cursor!r}"
            ) from error


def _parse_transaction(raw_transaction: object) -> Transaction:
    if not isinstance(raw_transaction, dict):
        raise ValueError("provider transactions must be objects")
    return Transaction(
        account_id=UUID(raw_transaction["account_id"]),
        provider_transaction_id=raw_transaction["provider_transaction_id"],
        amount_cents=raw_transaction["amount_cents"],
        description=raw_transaction["description"],
    )


def _parse_removal(raw_removal: object) -> TransactionRemoval:
    if not isinstance(raw_removal, dict):
        raise ValueError("provider removals must be objects")
    return TransactionRemoval(
        account_id=UUID(raw_removal["account_id"]),
        provider_transaction_id=raw_removal["provider_transaction_id"],
    )
