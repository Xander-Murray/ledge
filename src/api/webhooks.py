from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user_id, get_event_publisher, get_session
from application.event_queue import EventPublisher, EventPublishError
from persistence.inbound_events import (
    InboundEventConflictError,
    ProviderConnectionNotFoundError,
    accept_inbound_event,
)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
NonEmptyIdentifier = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]


class TransactionWebhookRequest(BaseModel):
    """Normalized notification that a provider connection has new activity."""

    model_config = ConfigDict(extra="forbid")

    provider_name: NonEmptyIdentifier
    provider_connection_id: NonEmptyIdentifier
    provider_event_id: NonEmptyIdentifier
    event_type: Literal["transactions.updated"]
    payload: dict[str, Any]


class AcceptedWebhookResponse(BaseModel):
    """Identity and durable state returned after accepting a notification."""

    id: UUID
    provider_event_id: str
    status: Literal["pending", "processing", "processed", "failed"]
    received_at: datetime


@router.post(
    "/transactions",
    response_model=AcceptedWebhookResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def accept_transaction_webhook(
    request: TransactionWebhookRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    event_publisher: Annotated[EventPublisher | None, Depends(get_event_publisher)],
) -> AcceptedWebhookResponse:
    """Durably accept a provider event without synchronizing in the request."""
    try:
        async with session.begin():
            event = await accept_inbound_event(
                session,
                user_id=user_id,
                provider_name=request.provider_name,
                provider_connection_id=request.provider_connection_id,
                provider_event_id=request.provider_event_id,
                event_type=request.event_type,
                raw_payload=request.payload,
            )
    except ProviderConnectionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="provider connection not found",
        ) from error
    except InboundEventConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="provider event identity conflicts with existing data",
        ) from error
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        ) from error

    if event_publisher is not None and event.status in {"pending", "failed"}:
        try:
            await event_publisher.publish(event.id)
        except EventPublishError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="event queue unavailable",
            ) from error

    return AcceptedWebhookResponse(
        id=event.id,
        provider_event_id=event.provider_event_id,
        status=event.status,
        received_at=event.received_at,
    )
