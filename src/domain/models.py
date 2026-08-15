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

    def __post_init__(self) -> None:
        if not self.provider_transaction_id.strip():
            raise ValueError("provider_transaction_id must not be empty")
        _validate_amount_cents(self.amount_cents)


@dataclass(frozen=True, slots=True)
class Posting:
    """One signed debit (positive) or credit (negative)."""

    ledger_account: str
    amount_cents: int

    def __post_init__(self) -> None:
        if not self.ledger_account.strip():
            raise ValueError("ledger_account must not be empty")
        _validate_amount_cents(self.amount_cents)


def _validate_amount_cents(amount_cents: int) -> None:
    if isinstance(amount_cents, bool) or not isinstance(amount_cents, int):
        raise TypeError("amount_cents must be an integer")
