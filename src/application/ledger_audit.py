"""Read-only integrity measurements for an owned slice of the ledger."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from persistence.models import (
    ExternalTransactionModel,
    JournalEntryModel,
    PostingModel,
)


class LedgerAuditError(RuntimeError):
    """Persisted ledger invariants failed a runtime audit."""


@dataclass(frozen=True, slots=True)
class LedgerAudit:
    transaction_count: int
    active_transaction_count: int
    pending_transaction_count: int
    removed_transaction_count: int
    replaced_transaction_count: int
    journal_count: int
    posting_count: int
    unsealed_journal_count: int
    unbalanced_journal_count: int

    def verify(self) -> None:
        status_total = (
            self.active_transaction_count
            + self.removed_transaction_count
            + self.replaced_transaction_count
        )
        if status_total != self.transaction_count:
            raise LedgerAuditError("Transaction status counts do not match total")
        if self.pending_transaction_count > self.active_transaction_count:
            raise LedgerAuditError("Pending transaction count exceeds active count")
        if self.unsealed_journal_count:
            raise LedgerAuditError("Ledger contains unsealed journal entries")
        if self.unbalanced_journal_count:
            raise LedgerAuditError("Ledger contains unbalanced journal entries")
        if self.posting_count != self.journal_count * 2:
            raise LedgerAuditError("Each journal entry must contain two postings")


def read_ledger_audit(session: Session, *, user_id: UUID) -> LedgerAudit:
    """Count persisted records and journal violations for one user."""
    owned_journals = (
        select(JournalEntryModel.id)
        .join(JournalEntryModel.external_transaction)
        .where(ExternalTransactionModel.user_id == user_id)
        .subquery()
    )
    unbalanced_journals = (
        select(PostingModel.journal_entry_id)
        .where(PostingModel.journal_entry_id.in_(select(owned_journals.c.id)))
        .group_by(PostingModel.journal_entry_id)
        .having(func.sum(PostingModel.amount_cents) != 0)
        .subquery()
    )
    return LedgerAudit(
        transaction_count=session.scalar(
            select(func.count(ExternalTransactionModel.id)).where(
                ExternalTransactionModel.user_id == user_id
            )
        )
        or 0,
        active_transaction_count=session.scalar(
            select(func.count(ExternalTransactionModel.id)).where(
                ExternalTransactionModel.user_id == user_id,
                ExternalTransactionModel.status == "active",
            )
        )
        or 0,
        pending_transaction_count=session.scalar(
            select(func.count(ExternalTransactionModel.id)).where(
                ExternalTransactionModel.user_id == user_id,
                ExternalTransactionModel.status == "active",
                ExternalTransactionModel.is_pending.is_(True),
            )
        )
        or 0,
        removed_transaction_count=session.scalar(
            select(func.count(ExternalTransactionModel.id)).where(
                ExternalTransactionModel.user_id == user_id,
                ExternalTransactionModel.status == "removed",
            )
        )
        or 0,
        replaced_transaction_count=session.scalar(
            select(func.count(ExternalTransactionModel.id)).where(
                ExternalTransactionModel.user_id == user_id,
                ExternalTransactionModel.status == "replaced",
            )
        )
        or 0,
        journal_count=session.scalar(select(func.count()).select_from(owned_journals))
        or 0,
        posting_count=session.scalar(
            select(func.count(PostingModel.id)).where(
                PostingModel.journal_entry_id.in_(select(owned_journals.c.id))
            )
        )
        or 0,
        unsealed_journal_count=session.scalar(
            select(func.count(JournalEntryModel.id))
            .join(JournalEntryModel.external_transaction)
            .where(
                ExternalTransactionModel.user_id == user_id,
                JournalEntryModel.sealed_at.is_(None),
            )
        )
        or 0,
        unbalanced_journal_count=session.scalar(
            select(func.count()).select_from(unbalanced_journals)
        )
        or 0,
    )
