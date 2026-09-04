from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user_id, get_session
from persistence.models import ExternalTransactionModel

router = APIRouter(prefix="/transactions", tags=["transactions"])
TransactionStatus = Literal["active", "removed", "replaced"]


class TransactionResponse(BaseModel):
    """Public representation of a provider transaction's current state."""

    id: UUID
    account_id: UUID
    provider_transaction_id: str
    amount_cents: int
    description: str
    is_pending: bool
    pending_provider_transaction_id: str | None
    status: TransactionStatus
    created_at: datetime
    updated_at: datetime


@router.get("", response_model=list[TransactionResponse])
async def list_transactions(
    session: Annotated[AsyncSession, Depends(get_session)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    account_id: UUID | None = None,
    transaction_status: Annotated[
        TransactionStatus | None,
        Query(alias="status"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[TransactionResponse]:
    """List the configured user's transactions with optional filtering."""
    statement = select(ExternalTransactionModel).where(
        ExternalTransactionModel.user_id == user_id
    )
    if account_id is not None:
        statement = statement.where(
            ExternalTransactionModel.financial_account_id == account_id
        )
    if transaction_status is not None:
        statement = statement.where(
            ExternalTransactionModel.status == transaction_status
        )

    statement = (
        statement.order_by(
            ExternalTransactionModel.updated_at.desc(),
            ExternalTransactionModel.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )

    try:
        transactions = (await session.scalars(statement)).all()
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        ) from error

    return [
        TransactionResponse(
            id=transaction.id,
            account_id=transaction.financial_account_id,
            provider_transaction_id=transaction.provider_transaction_id,
            amount_cents=transaction.amount_cents,
            description=transaction.description,
            is_pending=transaction.is_pending,
            pending_provider_transaction_id=(
                transaction.pending_provider_transaction_id
            ),
            status=transaction.status,
            created_at=transaction.created_at,
            updated_at=transaction.updated_at,
        )
        for transaction in transactions
    ]
