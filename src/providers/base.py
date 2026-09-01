from dataclasses import dataclass
from typing import Protocol

from domain.models import Transaction, TransactionRemoval


@dataclass(frozen=True, slots=True)
class TransactionSyncPage:
    """One normalized page of transaction changes from a provider."""

    added: tuple[Transaction, ...]
    modified: tuple[Transaction, ...]
    removed: tuple[TransactionRemoval, ...]
    next_cursor: str
    has_more: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "added", tuple(self.added))
        object.__setattr__(self, "modified", tuple(self.modified))
        object.__setattr__(self, "removed", tuple(self.removed))
        if not isinstance(self.next_cursor, str):
            raise TypeError("next_cursor must be a string")
        if not isinstance(self.has_more, bool):
            raise TypeError("has_more must be a boolean")
        if self.has_more and not self.next_cursor:
            raise ValueError("a page with more results must provide a next cursor")


class TransactionProvider(Protocol):
    """Port implemented by fake and external transaction providers."""

    def fetch_transaction_updates(
        self,
        cursor: str | None,
    ) -> TransactionSyncPage:
        """Return transaction changes following cursor."""
        ...
