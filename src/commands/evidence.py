"""Read committed ledger evidence for a single user's mapped accounts."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from persistence.models import ExternalTransactionModel, JournalEntryModel, PostingModel


def ledger_snapshot(session: Session, user_id: UUID, account_ids: set[UUID]) -> dict:
    transactions = session.scalars(
        select(ExternalTransactionModel)
        .where(
            ExternalTransactionModel.user_id == user_id,
            ExternalTransactionModel.financial_account_id.in_(account_ids),
        )
        .order_by(ExternalTransactionModel.provider_transaction_id)
    ).all()
    journals = session.scalars(
        select(JournalEntryModel)
        .join(ExternalTransactionModel)
        .where(
            ExternalTransactionModel.user_id == user_id,
            ExternalTransactionModel.financial_account_id.in_(account_ids),
        )
        .order_by(JournalEntryModel.id)
    ).all()
    postings = session.scalars(
        select(PostingModel)
        .join(JournalEntryModel)
        .join(ExternalTransactionModel)
        .where(
            ExternalTransactionModel.user_id == user_id,
            ExternalTransactionModel.financial_account_id.in_(account_ids),
        )
        .order_by(PostingModel.id)
    ).all()
    return {
        "transactions": {
            row.provider_transaction_id: {
                "amount_cents": row.amount_cents,
                "status": row.status,
                "pending": row.is_pending,
                "replaces": row.pending_provider_transaction_id,
            }
            for row in transactions
        },
        "journals": {
            str(row.id): {
                "reversal_of": str(row.reversal_of_entry_id)
                if row.reversal_of_entry_id
                else None,
                "sealed": row.sealed_at is not None,
            }
            for row in journals
        },
        "postings": [
            (str(row.journal_entry_id), row.ledger_account, row.amount_cents)
            for row in postings
        ],
    }


def print_changes(before: dict, after: dict) -> None:
    """Print committed changes; fetched provider counts alone are insufficient."""
    for identity, state in after["transactions"].items():
        previous = before["transactions"].get(identity)
        if previous != state:
            print(f"      {identity}: {previous} -> {state}", flush=True)
    print(
        f"      committed journals: {len(before['journals'])} -> "
        f"{len(after['journals'])}; postings: {len(before['postings'])} -> "
        f"{len(after['postings'])}",
        flush=True,
    )
    if before == after:
        print("      No committed financial changes.", flush=True)
