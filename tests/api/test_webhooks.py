from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from httpx2 import ASGITransport, AsyncClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from api.app import create_app
from persistence.database import AsyncSessionFactory
from persistence.models import InboundEventModel

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
FAKE_SYNC_STATE_ID = UUID("20000000-0000-0000-0000-000000000001")


def webhook_payload(
    *,
    provider_event_id: str = "event-1001",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "provider_name": "fake",
        "provider_connection_id": "sandbox-primary",
        "provider_event_id": provider_event_id,
        "event_type": "transactions.updated",
        "payload": (
            {"webhook_code": "SYNC_UPDATES_AVAILABLE", "item_id": "item-1"}
            if payload is None
            else payload
        ),
    }


@pytest.mark.integration
@pytest.mark.anyio
async def test_webhook_durably_preserves_the_raw_payload(
    api_database: str,
    api_client_factory: Callable[[UUID], AsyncIterator[AsyncClient]],
) -> None:
    request_body = webhook_payload()
    async for client in api_client_factory(USER_ID):
        response = await client.post("/webhooks/transactions", json=request_body)

    assert response.status_code == 202
    response_body = response.json()
    assert response_body["provider_event_id"] == "event-1001"
    assert response_body["status"] == "pending"
    assert response_body["received_at"] is not None

    engine = create_engine(api_database)
    try:
        with Session(engine) as session:
            event = session.scalar(select(InboundEventModel))

            assert event is not None
            assert str(event.id) == response_body["id"]
            assert event.transaction_sync_state_id == FAKE_SYNC_STATE_ID
            assert event.event_type == "transactions.updated"
            assert event.raw_payload == request_body["payload"]
            assert event.status == "pending"
            assert event.processed_at is None
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_identical_webhook_redelivery_returns_the_existing_event(
    api_database: str,
    api_client_factory: Callable[[UUID], AsyncIterator[AsyncClient]],
) -> None:
    request_body = webhook_payload()
    async for client in api_client_factory(USER_ID):
        first_response = await client.post(
            "/webhooks/transactions",
            json=request_body,
        )
        duplicate_response = await client.post(
            "/webhooks/transactions",
            json=request_body,
        )

    assert first_response.status_code == 202
    assert duplicate_response.status_code == 202
    assert duplicate_response.json() == first_response.json()

    engine = create_engine(api_database)
    try:
        with Session(engine) as session:
            assert session.scalar(select(func.count(InboundEventModel.id))) == 1
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_redelivery_reports_the_existing_events_current_status(
    api_database: str,
    api_client_factory: Callable[[UUID], AsyncIterator[AsyncClient]],
) -> None:
    request_body = webhook_payload()
    async for client in api_client_factory(USER_ID):
        first_response = await client.post(
            "/webhooks/transactions",
            json=request_body,
        )

        engine = create_engine(api_database)
        try:
            with Session(engine) as session, session.begin():
                event = session.scalar(select(InboundEventModel))
                assert event is not None
                event.status = "processed"
                event.attempt_count = 1
                event.processing_started_at = event.received_at
                event.processed_at = datetime.now(UTC)
        finally:
            engine.dispose()

        duplicate_response = await client.post(
            "/webhooks/transactions",
            json=request_body,
        )

    assert first_response.status_code == 202
    assert first_response.json()["status"] == "pending"
    assert duplicate_response.status_code == 202
    assert duplicate_response.json()["id"] == first_response.json()["id"]
    assert duplicate_response.json()["status"] == "processed"


@pytest.mark.integration
@pytest.mark.anyio
async def test_reused_webhook_identity_with_different_data_is_a_conflict(
    api_database: str,
    api_client_factory: Callable[[UUID], AsyncIterator[AsyncClient]],
) -> None:
    async for client in api_client_factory(USER_ID):
        first_response = await client.post(
            "/webhooks/transactions",
            json=webhook_payload(),
        )
        conflict_response = await client.post(
            "/webhooks/transactions",
            json=webhook_payload(payload={"item_id": "different-item"}),
        )

    assert first_response.status_code == 202
    assert conflict_response.status_code == 409
    assert conflict_response.json() == {
        "detail": "provider event identity conflicts with existing data"
    }

    engine = create_engine(api_database)
    try:
        with Session(engine) as session:
            assert session.scalar(select(func.count(InboundEventModel.id))) == 1
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_webhook_cannot_target_another_users_provider_connection(
    api_client_factory: Callable[[UUID], AsyncIterator[AsyncClient]],
) -> None:
    request_body = webhook_payload()
    request_body["provider_name"] = "private-provider"
    request_body["provider_connection_id"] = "other-user-connection"

    async for client in api_client_factory(USER_ID):
        response = await client.post("/webhooks/transactions", json=request_body)

    assert response.status_code == 404
    assert response.json() == {"detail": "provider connection not found"}


@pytest.mark.integration
@pytest.mark.anyio
async def test_webhook_rejects_invalid_envelopes(
    api_client_factory: Callable[[UUID], AsyncIterator[AsyncClient]],
) -> None:
    blank_provider = webhook_payload()
    blank_provider["provider_name"] = "   "
    extra_field = webhook_payload(provider_event_id="event-extra")
    extra_field["unexpected"] = True
    non_object_payload = webhook_payload(provider_event_id="event-array")
    non_object_payload["payload"] = ["not", "an", "object"]
    unsupported_event = webhook_payload(provider_event_id="event-unsupported")
    unsupported_event["event_type"] = "accounts.updated"

    async for client in api_client_factory(USER_ID):
        responses = [
            await client.post("/webhooks/transactions", json=blank_provider),
            await client.post("/webhooks/transactions", json=extra_field),
            await client.post("/webhooks/transactions", json=non_object_payload),
            await client.post("/webhooks/transactions", json=unsupported_event),
        ]

    assert all(response.status_code == 422 for response in responses)


@pytest.mark.anyio
async def test_webhook_returns_unavailable_when_database_query_fails() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.scalar.side_effect = OperationalError(
        "SELECT transaction_sync_states",
        {},
        Exception("database is offline"),
    )
    session.begin.return_value.__aenter__.return_value = session

    @asynccontextmanager
    async def session_context() -> AsyncIterator[AsyncSession]:
        yield session

    session_factory: AsyncSessionFactory = session_context
    app = create_app(session_factory=session_factory, user_id=USER_ID)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/webhooks/transactions",
            json=webhook_payload(),
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}
