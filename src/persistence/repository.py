from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from domain.ledger import (
    TransactionConflictError,
    TransactionNotFoundError,
    TransactionStateError,
    create_journal_entry,
    create_reversal_entry,
)
from domain.models import JournalEntry, Posting, Transaction, TransactionRemoval
from persistence.models import (
    ExternalTransactionModel,
    JournalEntryModel,
    PostingModel,
)


class LedgerRepository:
    """Translate domain ledger operations into staged database writes."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_transaction(self, *, user_id: UUID, transaction: Transaction) -> UUID:
        """Stage one external transaction and its sealed journal.

        The caller owns the surrounding transaction and decides whether to commit
        or roll back. Return the Ledge-owned external transaction ID.
        """

        if transaction.pending_provider_transaction_id is not None:
            raise TransactionStateError(
                f"Transaction {transaction.provider_transaction_id!r} must use the "
                "pending replacement operation"
            )

        external_transaction_id = uuid4()
        journal_entry = create_journal_entry(transaction, uuid4())

        existing_transaction = self._session.scalar(
            select(ExternalTransactionModel)
            .where(
                ExternalTransactionModel.user_id == user_id,
                ExternalTransactionModel.provider_transaction_id
                == transaction.provider_transaction_id,
            )
            .with_for_update()
        )

        if existing_transaction is not None:
            if existing_transaction.status == "active" and self._matches_transaction(
                existing_transaction, transaction
            ):
                return existing_transaction.id

            raise TransactionConflictError(
                f"Transaction {transaction.provider_transaction_id!r} "
                "already exists with different data"
            )
        external_transaction_model = ExternalTransactionModel(
            id=external_transaction_id,
            user_id=user_id,
            financial_account_id=transaction.account_id,
            provider_transaction_id=transaction.provider_transaction_id,
            amount_cents=transaction.amount_cents,
            description=transaction.description,
            is_pending=transaction.is_pending,
            pending_provider_transaction_id=(
                transaction.pending_provider_transaction_id
            ),
            status="active",
        )

        journal_entry_model = JournalEntryModel(
            id=journal_entry.journal_entry_id,
            external_transaction_id=external_transaction_id,
            description=journal_entry.description,
            reversal_of_entry_id=journal_entry.reversal_of_entry_id,
            sealed_at=None,
        )

        posting_models = [
            PostingModel(
                journal_entry_id=journal_entry.journal_entry_id,
                line_number=line_number,
                ledger_account=posting.ledger_account,
                amount_cents=posting.amount_cents,
            )
            for line_number, posting in enumerate(journal_entry.postings)
        ]

        self._session.add(external_transaction_model)
        self._session.add(journal_entry_model)
        self._session.add_all(posting_models)
        self._session.flush()

        journal_entry_model.sealed_at = datetime.now(UTC)
        self._session.flush()

        return external_transaction_id

    def modify_transaction(self, *, user_id: UUID, transaction: Transaction) -> UUID:
        """Stage a projection update, reversal, and replacement journal.

        The caller owns the surrounding transaction and decides whether to commit
        or roll back. Return the existing Ledge-owned external transaction ID.
        """
        existing = self._load_transaction_for_update(
            user_id=user_id,
            provider_transaction_id=transaction.provider_transaction_id,
        )

        if existing is None:
            raise TransactionNotFoundError(
                f"Transaction {transaction.provider_transaction_id!r} does not exist"
            )
        if existing.status != "active":
            raise TransactionStateError(
                f"Transaction {transaction.provider_transaction_id!r} with status "
                f"{existing.status!r} cannot be modified"
            )
        if (
            existing.is_pending != transaction.is_pending
            or existing.pending_provider_transaction_id
            != transaction.pending_provider_transaction_id
        ):
            raise TransactionStateError(
                f"Transaction {transaction.provider_transaction_id!r} "
                "settlement identity cannot be modified"
            )
        if self._matches_transaction(existing, transaction):
            return existing.id

        active_domain = self._get_active_journal_entry(existing)
        reversal_model = self._stage_journal_entry(
            external_transaction_id=existing.id,
            journal_entry=create_reversal_entry(active_domain, uuid4()),
        )
        replacement_model = self._stage_journal_entry(
            external_transaction_id=existing.id,
            journal_entry=create_journal_entry(transaction, uuid4()),
        )

        existing.financial_account_id = transaction.account_id
        existing.amount_cents = transaction.amount_cents
        existing.description = transaction.description
        self._session.flush()

        sealed_at = datetime.now(UTC)
        reversal_model.sealed_at = sealed_at
        replacement_model.sealed_at = sealed_at
        self._session.flush()

        return existing.id

    def remove_transaction(
        self,
        *,
        user_id: UUID,
        removal: TransactionRemoval,
    ) -> UUID:
        """Stage a projection removal and reversal of its active journal.

        The caller owns the surrounding transaction and decides whether to commit
        or roll back. Return the existing Ledge-owned external transaction ID.
        """
        existing = self._load_transaction_for_update(
            user_id=user_id,
            provider_transaction_id=removal.provider_transaction_id,
        )

        if existing is None:
            raise TransactionNotFoundError(
                f"Transaction {removal.provider_transaction_id!r} does not exist"
            )
        if existing.financial_account_id != removal.account_id:
            raise TransactionConflictError(
                f"Transaction {removal.provider_transaction_id!r} "
                "removal account differs from current data"
            )
        if existing.status == "removed":
            return existing.id
        if existing.status == "replaced":
            return existing.id
        if existing.status != "active":
            raise TransactionStateError(
                f"Transaction {removal.provider_transaction_id!r} "
                f"cannot be removed from status {existing.status!r}"
            )

        active_domain = self._get_active_journal_entry(existing)
        reversal_model = self._stage_journal_entry(
            external_transaction_id=existing.id,
            journal_entry=create_reversal_entry(active_domain, uuid4()),
        )

        existing.status = "removed"
        self._session.flush()

        reversal_model.sealed_at = datetime.now(UTC)
        self._session.flush()

        return existing.id

    def replace_pending_transaction(
        self,
        *,
        user_id: UUID,
        posted_transaction: Transaction,
    ) -> UUID:
        """Stage replacement of one pending transaction by its posted version."""
        pending_provider_id = posted_transaction.pending_provider_transaction_id
        if posted_transaction.is_pending or pending_provider_id is None:
            raise TransactionStateError(
                f"Transaction {posted_transaction.provider_transaction_id!r} "
                "is not a posted replacement"
            )

        pending = self._load_transaction_for_update(
            user_id=user_id,
            provider_transaction_id=pending_provider_id,
        )
        existing_posted = self._load_transaction_for_update(
            user_id=user_id,
            provider_transaction_id=posted_transaction.provider_transaction_id,
        )

        if existing_posted is not None:
            if (
                pending is not None
                and pending.status == "replaced"
                and existing_posted.status == "active"
                and self._matches_transaction(existing_posted, posted_transaction)
            ):
                return existing_posted.id
            raise TransactionConflictError(
                f"Transaction {posted_transaction.provider_transaction_id!r} "
                "already exists with different state"
            )
        if pending is None:
            raise TransactionNotFoundError(
                f"Pending transaction {pending_provider_id!r} does not exist"
            )
        if pending.financial_account_id != posted_transaction.account_id:
            raise TransactionConflictError(
                f"Pending transaction {pending_provider_id!r} "
                "belongs to a different account"
            )
        if not pending.is_pending:
            raise TransactionStateError(
                f"Transaction {pending_provider_id!r} is not pending"
            )
        if pending.status != "active":
            raise TransactionStateError(
                f"Pending transaction {pending_provider_id!r} with status "
                f"{pending.status!r} cannot be replaced"
            )

        pending_domain = self._get_active_journal_entry(pending)
        posted_transaction_id = uuid4()
        reversal_model = self._stage_journal_entry(
            external_transaction_id=pending.id,
            journal_entry=create_reversal_entry(pending_domain, uuid4()),
        )
        posted_journal_model = self._stage_journal_entry(
            external_transaction_id=posted_transaction_id,
            journal_entry=create_journal_entry(posted_transaction, uuid4()),
        )
        self._session.add(
            ExternalTransactionModel(
                id=posted_transaction_id,
                user_id=user_id,
                financial_account_id=posted_transaction.account_id,
                provider_transaction_id=(posted_transaction.provider_transaction_id),
                amount_cents=posted_transaction.amount_cents,
                description=posted_transaction.description,
                is_pending=False,
                pending_provider_transaction_id=pending_provider_id,
                status="active",
            )
        )
        pending.status = "replaced"
        self._session.flush()

        sealed_at = datetime.now(UTC)
        reversal_model.sealed_at = sealed_at
        posted_journal_model.sealed_at = sealed_at
        self._session.flush()

        return posted_transaction_id

    def _load_transaction_for_update(
        self,
        *,
        user_id: UUID,
        provider_transaction_id: str,
    ) -> ExternalTransactionModel | None:
        return self._session.scalar(
            select(ExternalTransactionModel)
            .where(
                ExternalTransactionModel.user_id == user_id,
                ExternalTransactionModel.provider_transaction_id
                == provider_transaction_id,
            )
            .options(
                selectinload(ExternalTransactionModel.journal_entries).selectinload(
                    JournalEntryModel.postings
                )
            )
            .with_for_update()
        )

    @staticmethod
    def _matches_transaction(
        model: ExternalTransactionModel,
        transaction: Transaction,
    ) -> bool:
        return (
            model.financial_account_id == transaction.account_id
            and model.amount_cents == transaction.amount_cents
            and model.description == transaction.description
            and model.is_pending == transaction.is_pending
            and model.pending_provider_transaction_id
            == transaction.pending_provider_transaction_id
        )

    @staticmethod
    def _get_active_journal_entry(
        transaction: ExternalTransactionModel,
    ) -> JournalEntry:
        reversed_ids = {
            entry.reversal_of_entry_id
            for entry in transaction.journal_entries
            if entry.reversal_of_entry_id is not None
        }
        active_models = [
            entry
            for entry in transaction.journal_entries
            if entry.reversal_of_entry_id is None and entry.id not in reversed_ids
        ]
        if len(active_models) != 1:
            raise TransactionStateError(
                f"Transaction {transaction.provider_transaction_id!r} "
                "must have exactly one active journal entry; "
                f"found {len(active_models)}"
            )

        active_model = active_models[0]
        return JournalEntry(
            journal_entry_id=active_model.id,
            source_provider_transaction_id=transaction.provider_transaction_id,
            description=active_model.description,
            postings=tuple(
                Posting(
                    ledger_account=posting.ledger_account,
                    amount_cents=posting.amount_cents,
                )
                for posting in sorted(
                    active_model.postings, key=lambda posting: posting.line_number
                )
            ),
            reversal_of_entry_id=active_model.reversal_of_entry_id,
        )

    def _stage_journal_entry(
        self,
        *,
        external_transaction_id: UUID,
        journal_entry: JournalEntry,
    ) -> JournalEntryModel:
        model = JournalEntryModel(
            id=journal_entry.journal_entry_id,
            external_transaction_id=external_transaction_id,
            description=journal_entry.description,
            reversal_of_entry_id=journal_entry.reversal_of_entry_id,
            sealed_at=None,
        )
        postings = [
            PostingModel(
                journal_entry_id=journal_entry.journal_entry_id,
                line_number=line_number,
                ledger_account=posting.ledger_account,
                amount_cents=posting.amount_cents,
            )
            for line_number, posting in enumerate(journal_entry.postings)
        ]
        self._session.add(model)
        self._session.add_all(postings)
        return model
