from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from httpx2 import ASGITransport, AsyncClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from api.app import create_app
from persistence.database import AsyncSessionFactory

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
EMPTY_USER_ID = UUID("33333333-3333-3333-3333-333333333333")


@pytest.mark.integration
@pytest.mark.anyio
async def test_sync_statuses_are_scoped_and_ordered_for_the_configured_user(
    api_client_factory: Callable[[UUID], AsyncIterator[AsyncClient]],
) -> None:
    async for client in api_client_factory(USER_ID):
        response = await client.get("/sync-status")

    assert response.status_code == 200
    sync_statuses = response.json()
    assert [status["provider_name"] for status in sync_statuses] == [
        "fake",
        "plaid",
    ]
    assert sync_statuses[0]["provider_connection_id"] == "sandbox-primary"
    assert sync_statuses[0]["cursor"] == "fake-cursor-12"
    assert sync_statuses[1]["cursor"] is None
    assert all("user_id" not in status for status in sync_statuses)


@pytest.mark.integration
@pytest.mark.anyio
async def test_sync_statuses_returns_empty_list_for_user_without_connections(
    api_client_factory: Callable[[UUID], AsyncIterator[AsyncClient]],
) -> None:
    async for client in api_client_factory(EMPTY_USER_ID):
        response = await client.get("/sync-status")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.anyio
async def test_sync_status_returns_unavailable_when_database_query_fails() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.scalars.side_effect = OperationalError(
        "SELECT transaction_sync_states",
        {},
        Exception("database is offline"),
    )

    @asynccontextmanager
    async def session_context() -> AsyncIterator[AsyncSession]:
        yield session

    session_factory: AsyncSessionFactory = session_context
    app = create_app(session_factory=session_factory, user_id=USER_ID)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/sync-status")

    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}
