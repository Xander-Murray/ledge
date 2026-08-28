from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.ledger import TransactionConflictError, create_journal_entry
from domain.models import Transaction
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
