from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Transaction:
    """A provider transaction translated into Ledge's domain language."""

    account_id: UUID
    provider_transaction_id: str
    amount_cents: int
    description: str


@dataclass(frozen=True, slots=True)
class Posting:
    """One signed debit (positive) or credit (negative)."""

    ledger_account: str
    amount_cents: int
