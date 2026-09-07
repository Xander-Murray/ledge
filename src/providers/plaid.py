"""Sandbox-only Plaid adapter for one Item and its mapped Ledge accounts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal
from types import MappingProxyType
from uuid import UUID

import httpx2

from domain.models import Transaction, TransactionRemoval
from providers.base import ProviderError, TransactionSyncPage


class PlaidResponseError(ProviderError):
    """Plaid returned data that cannot safely enter the ledger."""


class PlaidPaginationMutationError(ProviderError):
    """Discard fetched pages and retry from the last committed cursor."""


class PlaidTransactionProvider:
    """Fetch normalized USD updates using an injected, caller-owned HTTP client.

    Construct one adapter per Plaid Item. Account mappings must point to existing
    accounts owned by that Item's Ledge user. Tokens never enter domain values.
    """

    def __init__(
        self,
        *,
        client: httpx2.Client,
        client_id: str,
        secret: str,
        access_token: str,
        account_ids: Mapping[str, UUID],
    ) -> None:
        if any(not value.strip() for value in (client_id, secret, access_token)):
            raise ValueError("Plaid credentials must not be empty")
        if any(
            not key.strip() or not isinstance(value, UUID)
            for key, value in account_ids.items()
        ):
            raise ValueError("Plaid account mappings require nonempty IDs and UUIDs")
        self._client = client
        self._credentials = {
            "client_id": client_id,
            "secret": secret,
            "access_token": access_token,
        }
        self._account_ids = MappingProxyType(dict(account_ids))

    def fetch_transaction_updates(self, cursor: str | None) -> TransactionSyncPage:
        body: dict[str, object] = {**self._credentials, "count": 500}
        if cursor is not None:
            body["cursor"] = cursor
        try:
            response = self._client.post(
                "https://sandbox.plaid.com/transactions/sync",
                json=body,
                headers={"Plaid-Version": "2020-09-14"},
                timeout=30.0,
                follow_redirects=False,
            )
        except httpx2.RequestError:
            raise ProviderError("Plaid request failed") from None

        try:
            # Parse JSON decimal literals directly, before binary float rounding.
            payload = json.loads(response.content, parse_float=Decimal)
        except (ValueError, UnicodeError):
            raise PlaidResponseError("Plaid returned invalid JSON") from None
        if not isinstance(payload, dict):
            raise PlaidResponseError("Plaid returned an invalid response object")
        if not response.is_success:
            if (
                payload.get("error_code")
                == "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"
            ):
                raise PlaidPaginationMutationError("Plaid pagination must restart")
            raise ProviderError("Plaid rejected the sync request")

        try:
            return TransactionSyncPage(
                added=tuple(self._transaction(row) for row in _rows(payload, "added")),
                modified=tuple(
                    self._transaction(row) for row in _rows(payload, "modified")
                ),
                removed=tuple(
                    TransactionRemoval(
                        account_id=self._account(row),
                        provider_transaction_id=_text(row, "transaction_id"),
                    )
                    for row in _rows(payload, "removed")
                ),
                next_cursor=_text(payload, "next_cursor", allow_empty=True),
                has_more=payload["has_more"],
            )
        except (KeyError, TypeError, ValueError):
            raise PlaidResponseError(
                "Plaid returned invalid transaction data"
            ) from None

    def _account(self, row: dict) -> UUID:
        return self._account_ids[_text(row, "account_id")]

    def _transaction(self, row: dict) -> Transaction:
        if row.get("iso_currency_code") != "USD":
            raise ValueError("Only USD transactions are supported")
        amount = row["amount"]
        if isinstance(amount, bool) or not isinstance(amount, (int, Decimal)):
            raise ValueError("Amount must be a JSON number")
        decimal_amount = Decimal(amount)
        if not decimal_amount.is_finite() or decimal_amount.copy_abs() >= 2**63:
            raise ValueError("Amount is outside the supported range")
        # Shifting the exponent avoids Decimal context rounding on long inputs.
        sign, digits, exponent = decimal_amount.as_tuple()
        assert isinstance(exponent, int)
        cents = Decimal((sign, digits, exponent + 2))
        if not cents.is_finite() or cents != cents.to_integral_value():
            raise ValueError("Amount must be exact integer cents")
        if not -(2**63) < cents < 2**63:
            raise ValueError("Amount exceeds reversible BIGINT range")
        pending_id = row.get("pending_transaction_id")
        if pending_id is not None:
            pending_id = _text(row, "pending_transaction_id")
        return Transaction(
            account_id=self._account(row),
            provider_transaction_id=_text(row, "transaction_id"),
            amount_cents=int(cents),
            description=_text(row, "name", allow_empty=True),
            is_pending=row["pending"],
            pending_provider_transaction_id=pending_id,
        )


def _text(row: dict, key: str, *, allow_empty: bool = False) -> str:
    value = row[key]
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError("Invalid string field")
    return value


def _rows(payload: dict, key: str) -> list[dict]:
    rows = payload[key]
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Invalid transaction array")
    return rows
