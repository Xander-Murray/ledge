from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user_id, get_session
from persistence.models import FinancialAccountModel

router = APIRouter(prefix="/accounts", tags=["accounts"])


class AccountResponse(BaseModel):
    """Public representation of one financial account."""

    id: UUID
    name: str
    account_type: Literal["checking", "savings", "credit"]
    created_at: datetime


@router.get("", response_model=list[AccountResponse])
async def list_accounts(
    session: Annotated[AsyncSession, Depends(get_session)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
) -> list[AccountResponse]:
    """List the financial accounts owned by the configured user."""
    statement = (
        select(FinancialAccountModel)
        .where(FinancialAccountModel.user_id == user_id)
        .order_by(FinancialAccountModel.name, FinancialAccountModel.id)
    )

    try:
        accounts = (await session.scalars(statement)).all()
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        ) from error

    return [
        AccountResponse(
            id=account.id,
            name=account.name,
            account_type=account.account_type,
            created_at=account.created_at,
        )
        for account in accounts
    ]
