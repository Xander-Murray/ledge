from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

from domain.invariants import assert_balanced


@dataclass(frozen=True, slots=True)
class Transaction:
    """A provider transaction translated into Ledge's domain language."""

    account_id: UUID
    provider_transaction_id: str
    amount_cents: int
    description: str
    is_pending: bool = False
    pending_provider_transaction_id: str | None = None

    def __post_init__(self) -> None:
        if not self.provider_transaction_id.strip():
            raise ValueError("provider_transaction_id must not be empty")
        _validate_amount_cents(self.amount_cents)
        if not isinstance(self.is_pending, bool):
            raise TypeError("is_pending must be a boolean")
        if self.pending_provider_transaction_id is not None:
            if not self.pending_provider_transaction_id.strip():
                raise ValueError("pending_provider_transaction_id must not be empty")
            if self.pending_provider_transaction_id == self.provider_transaction_id:
                raise ValueError("a transaction cannot replace itself")
            if self.is_pending:
                raise ValueError(
                    "a pending transaction cannot replace another transaction"
                )


@dataclass(frozen=True, slots=True)
class TransactionRemoval:
    """The identity a provider supplies when a transaction is removed."""

    account_id: UUID
    provider_transaction_id: str

    def __post_init__(self) -> None:
        if not self.provider_transaction_id.strip():
            raise ValueError("provider_transaction_id must not be empty")


@dataclass(frozen=True, slots=True)
class Posting:
    """One signed debit (positive) or credit (negative)."""

    ledger_account: str
    amount_cents: int

    def __post_init__(self) -> None:
        if not self.ledger_account.strip():
            raise ValueError("ledger_account must not be empty")
        _validate_amount_cents(self.amount_cents)


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """An immutable, balanced record of one accounting event."""

    journal_entry_id: UUID
    source_provider_transaction_id: str
    description: str
    postings: tuple[Posting, ...]
    reversal_of_entry_id: UUID | None = None

    def __post_init__(self) -> None:
        if not self.source_provider_transaction_id.strip():
            raise ValueError("source_provider_transaction_id must not be empty")
        postings = tuple(self.postings)
        assert_balanced(postings)
        object.__setattr__(self, "postings", postings)


@dataclass(frozen=True, slots=True)
class LedgerState:
    """An immutable snapshot of current transactions and journal history."""

    transactions_by_provider_id: Mapping[str, Transaction]
    journal_entries: tuple[JournalEntry, ...]
    removed_provider_transaction_ids: frozenset[str] = frozenset()
    replaced_provider_transaction_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        transactions = dict(self.transactions_by_provider_id)
        journal_entries = tuple(self.journal_entries)
        removed_provider_ids = frozenset(self.removed_provider_transaction_ids)
        replaced_provider_ids = frozenset(self.replaced_provider_transaction_ids)
        for provider_id, transaction in transactions.items():
            if provider_id != transaction.provider_transaction_id:
                raise ValueError(
                    "Transaction mapping keys must match provider transaction IDs"
                )

        journal_entry_ids = [entry.journal_entry_id for entry in journal_entries]
        if len(journal_entry_ids) != len(set(journal_entry_ids)):
            raise ValueError("Journal entry IDs must be unique")
        unknown_removed_ids = removed_provider_ids.difference(transactions)
        if unknown_removed_ids:
            raise ValueError(
                "Removed transaction IDs must exist in the transaction map"
            )
        unknown_replaced_ids = replaced_provider_ids.difference(transactions)
        if unknown_replaced_ids:
            raise ValueError(
                "Replaced transaction IDs must exist in the transaction map"
            )
        if removed_provider_ids & replaced_provider_ids:
            raise ValueError("A transaction cannot be both removed and replaced")
        if any(
            not transactions[provider_id].is_pending
            for provider_id in replaced_provider_ids
        ):
            raise ValueError("Only pending transactions can be marked replaced")

        replacement_sources = [
            transaction.pending_provider_transaction_id
            for transaction in transactions.values()
            if transaction.pending_provider_transaction_id is not None
        ]
        if len(replacement_sources) != len(set(replacement_sources)):
            raise ValueError("A pending transaction can have only one replacement")
        if set(replacement_sources) != replaced_provider_ids:
            raise ValueError(
                "Replaced transaction IDs must match posted transaction links"
            )
        for transaction in transactions.values():
            pending_id = transaction.pending_provider_transaction_id
            if pending_id is None:
                continue
            pending_transaction = transactions.get(pending_id)
            if pending_transaction is None:
                raise ValueError(
                    "A posted replacement must reference a known transaction"
                )
            if not pending_transaction.is_pending:
                raise ValueError(
                    "A posted replacement must reference a pending transaction"
                )
            if pending_transaction.account_id != transaction.account_id:
                raise ValueError(
                    "A posted replacement must use the pending transaction account"
                )

        object.__setattr__(
            self,
            "transactions_by_provider_id",
            MappingProxyType(transactions),
        )
        object.__setattr__(self, "journal_entries", journal_entries)
        object.__setattr__(
            self,
            "removed_provider_transaction_ids",
            removed_provider_ids,
        )
        object.__setattr__(
            self,
            "replaced_provider_transaction_ids",
            replaced_provider_ids,
        )

    @classmethod
    def empty(cls) -> LedgerState:
        return cls(transactions_by_provider_id={}, journal_entries=())


def _validate_amount_cents(amount_cents: int) -> None:
    if isinstance(amount_cents, bool) or not isinstance(amount_cents, int):
        raise TypeError("amount_cents must be an integer")
