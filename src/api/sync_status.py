from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user_id, get_session
from persistence.models import TransactionSyncStateModel

router = APIRouter(prefix="/sync-status", tags=["synchronization"])


class SyncStatusResponse(BaseModel):
    """Public progress information for one provider connection."""

    id: UUID
    provider_name: str
    provider_connection_id: str
    cursor: str | None
    created_at: datetime
    updated_at: datetime


@router.get("", response_model=list[SyncStatusResponse])
async def list_sync_statuses(
    session: Annotated[AsyncSession, Depends(get_session)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
) -> list[SyncStatusResponse]:
    """List synchronization progress for the configured user's connections."""
    statement = (
        select(TransactionSyncStateModel)
        .where(TransactionSyncStateModel.user_id == user_id)
        .order_by(
            TransactionSyncStateModel.provider_name,
            TransactionSyncStateModel.provider_connection_id,
        )
    )

    try:
        sync_states = (await session.scalars(statement)).all()
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        ) from error

    return [
        SyncStatusResponse(
            id=sync_state.id,
            provider_name=sync_state.provider_name,
            provider_connection_id=sync_state.provider_connection_id,
            cursor=sync_state.cursor,
            created_at=sync_state.created_at,
            updated_at=sync_state.updated_at,
        )
        for sync_state in sync_states
    ]
