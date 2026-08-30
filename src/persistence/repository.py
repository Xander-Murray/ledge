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
from domain.models import JournalEntry, Posting, Transaction
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
            if (
                existing_transaction.status == "active"
                and existing_transaction.amount_cents == transaction.amount_cents
                and existing_transaction.description == transaction.description
                and existing_transaction.financial_account_id == transaction.account_id
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
        existing = self._session.scalar(
            select(ExternalTransactionModel)
            .where(
                ExternalTransactionModel.user_id == user_id,
                ExternalTransactionModel.provider_transaction_id
                == transaction.provider_transaction_id,
            )
            .options(
                selectinload(ExternalTransactionModel.journal_entries).selectinload(
                    JournalEntryModel.postings
                )
            )
            .with_for_update()
        )

        if existing is None:
            raise TransactionNotFoundError(
                f"Transaction {transaction.provider_transaction_id!r} does not exist"
            )
        if existing.status == "removed":
            raise TransactionStateError(
                f"Removed transaction {transaction.provider_transaction_id!r} "
                "cannot be modified"
            )
        if (
            existing.financial_account_id == transaction.account_id
            and existing.amount_cents == transaction.amount_cents
            and existing.description == transaction.description
        ):
            return existing.id

        reversed_ids = {
            entry.reversal_of_entry_id
            for entry in existing.journal_entries
            if entry.reversal_of_entry_id is not None
        }
        active_models = [
            entry
            for entry in existing.journal_entries
            if entry.reversal_of_entry_id is None and entry.id not in reversed_ids
        ]
        if len(active_models) != 1:
            raise TransactionStateError(
                f"Transaction {transaction.provider_transaction_id!r} "
                "must have exactly one active journal entry; "
                f"found {len(active_models)}"
            )
        active_model = active_models[0]

        active_domain = JournalEntry(
            journal_entry_id=active_model.id,
            source_provider_transaction_id=existing.provider_transaction_id,
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
