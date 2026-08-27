from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from domain.ledger import create_journal_entry
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
