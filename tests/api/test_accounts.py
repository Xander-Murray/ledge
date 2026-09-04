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
async def test_accounts_are_scoped_to_the_configured_user(
    api_client_factory: Callable[[UUID], AsyncIterator[AsyncClient]],
) -> None:
    async for client in api_client_factory(USER_ID):
        response = await client.get("/accounts")

    assert response.status_code == 200
    accounts = response.json()
    assert [account["name"] for account in accounts] == [
        "Everyday Checking",
        "Rainy Day Savings",
    ]
    assert [account["account_type"] for account in accounts] == [
        "checking",
        "savings",
    ]
    assert all("user_id" not in account for account in accounts)


@pytest.mark.integration
@pytest.mark.anyio
async def test_accounts_returns_empty_list_when_user_has_no_accounts(
    api_client_factory: Callable[[UUID], AsyncIterator[AsyncClient]],
) -> None:
    async for client in api_client_factory(EMPTY_USER_ID):
        response = await client.get("/accounts")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.anyio
async def test_accounts_returns_service_unavailable_when_database_query_fails() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.scalars.side_effect = OperationalError(
        "SELECT financial_accounts",
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
        response = await client.get("/accounts")

    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}
