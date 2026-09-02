from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql import Select

from persistence.models import TransactionSyncStateModel
from persistence.repository import LedgerRepository
from providers.base import TransactionProvider, TransactionSyncPage


class SyncStateNotFoundError(LookupError):
    """Raised when synchronization targets an unknown provider connection."""


class SyncCursorConflictError(RuntimeError):
    """Raised when another worker advances a cursor during page fetching."""


class ProviderPaginationError(RuntimeError):
    """Raised when provider pagination repeats a cursor without finishing."""


@dataclass(frozen=True, slots=True)
class SyncResult:
    """Counts and cursor movement from one completed synchronization."""

    starting_cursor: str | None
    ending_cursor: str
    pages_fetched: int
    added_count: int
    modified_count: int
    removed_count: int


class TransactionSynchronizer:
    """Fetch and atomically persist one provider connection's complete update."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        provider: TransactionProvider,
    ) -> None:
        self._session_factory = session_factory
        self._provider = provider

    def synchronize(
        self,
        *,
        user_id: UUID,
        provider_name: str,
        provider_connection_id: str,
    ) -> SyncResult:
        starting_cursor = self._read_cursor(
            user_id=user_id,
            provider_name=provider_name,
            provider_connection_id=provider_connection_id,
        )
        pages = self._fetch_all_pages(starting_cursor)
        ending_cursor = pages[-1].next_cursor

        with self._session_factory() as session, session.begin():
            sync_state = session.scalar(
                self._sync_state_query(
                    user_id=user_id,
                    provider_name=provider_name,
                    provider_connection_id=provider_connection_id,
                ).with_for_update()
            )
            if sync_state is None:
                raise SyncStateNotFoundError(
                    self._missing_state_message(
                        user_id=user_id,
                        provider_name=provider_name,
                        provider_connection_id=provider_connection_id,
                    )
                )
            if sync_state.cursor != starting_cursor:
                raise SyncCursorConflictError(
                    f"Provider connection {provider_name!r}/"
                    f"{provider_connection_id!r} advanced from "
                    f"{starting_cursor!r} to {sync_state.cursor!r} during fetch"
                )

            repository = LedgerRepository(session)
            for page in pages:
                for transaction in page.added:
                    repository.add_transaction(
                        user_id=user_id,
                        transaction=transaction,
                    )
                for transaction in page.modified:
                    repository.modify_transaction(
                        user_id=user_id,
                        transaction=transaction,
                    )
                for removal in page.removed:
                    repository.remove_transaction(
                        user_id=user_id,
                        removal=removal,
                    )

            sync_state.cursor = ending_cursor
            session.flush()

        return SyncResult(
            starting_cursor=starting_cursor,
            ending_cursor=ending_cursor,
            pages_fetched=len(pages),
            added_count=sum(len(page.added) for page in pages),
            modified_count=sum(len(page.modified) for page in pages),
            removed_count=sum(len(page.removed) for page in pages),
        )

    def _read_cursor(
        self,
        *,
        user_id: UUID,
        provider_name: str,
        provider_connection_id: str,
    ) -> str | None:
        with self._session_factory() as session:
            sync_state = session.scalar(
                self._sync_state_query(
                    user_id=user_id,
                    provider_name=provider_name,
                    provider_connection_id=provider_connection_id,
                )
            )
            if sync_state is None:
                raise SyncStateNotFoundError(
                    self._missing_state_message(
                        user_id=user_id,
                        provider_name=provider_name,
                        provider_connection_id=provider_connection_id,
                    )
                )
            return sync_state.cursor

    def _fetch_all_pages(
        self,
        starting_cursor: str | None,
    ) -> tuple[TransactionSyncPage, ...]:
        pages: list[TransactionSyncPage] = []
        requested_cursors: set[str | None] = set()
        cursor = starting_cursor

        while True:
            if cursor in requested_cursors:
                raise ProviderPaginationError(
                    f"Provider repeated cursor {cursor!r} before pagination completed"
                )
            requested_cursors.add(cursor)

            page = self._provider.fetch_transaction_updates(cursor)
            pages.append(page)
            if not page.has_more:
                return tuple(pages)
            cursor = page.next_cursor

    @staticmethod
    def _sync_state_query(
        *,
        user_id: UUID,
        provider_name: str,
        provider_connection_id: str,
    ) -> Select[tuple[TransactionSyncStateModel]]:
        return select(TransactionSyncStateModel).where(
            TransactionSyncStateModel.user_id == user_id,
            TransactionSyncStateModel.provider_name == provider_name,
            TransactionSyncStateModel.provider_connection_id == provider_connection_id,
        )

    @staticmethod
    def _missing_state_message(
        *,
        user_id: UUID,
        provider_name: str,
        provider_connection_id: str,
    ) -> str:
        return (
            f"Sync state for user {user_id} and provider connection "
            f"{provider_name!r}/{provider_connection_id!r} does not exist"
        )
