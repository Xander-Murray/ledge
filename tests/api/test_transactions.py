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


@pytest.mark.integration
@pytest.mark.anyio
async def test_transactions_are_scoped_and_ordered_for_the_configured_user(
    api_client_factory: Callable[[UUID], AsyncIterator[AsyncClient]],
) -> None:
    async for client in api_client_factory(USER_ID):
        response = await client.get("/transactions")

    assert response.status_code == 200
    transactions = response.json()
    assert [transaction["provider_transaction_id"] for transaction in transactions] == [
        "provider-coffee-pending",
        "provider-grocery",
        "provider-removed",
    ]
    assert transactions[0]["is_pending"] is True
    assert transactions[0]["amount_cents"] == 675
    assert all("user_id" not in transaction for transaction in transactions)


@pytest.mark.integration
@pytest.mark.anyio
async def test_transactions_support_account_and_status_filters(
    api_client_factory: Callable[[UUID], AsyncIterator[AsyncClient]],
) -> None:
    async for client in api_client_factory(USER_ID):
        account_response = await client.get(
            "/transactions",
            params={"account_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"},
        )
        status_response = await client.get(
            "/transactions",
            params={"status": "removed"},
        )

    assert account_response.status_code == 200
    assert [
        transaction["provider_transaction_id"]
        for transaction in account_response.json()
    ] == ["provider-coffee-pending", "provider-grocery"]
    assert status_response.status_code == 200
    assert [
        transaction["provider_transaction_id"] for transaction in status_response.json()
    ] == ["provider-removed"]


@pytest.mark.integration
@pytest.mark.anyio
async def test_transactions_support_bounded_pagination(
    api_client_factory: Callable[[UUID], AsyncIterator[AsyncClient]],
) -> None:
    async for client in api_client_factory(USER_ID):
        page_response = await client.get(
            "/transactions",
            params={"limit": 1, "offset": 1},
        )
        invalid_responses = [
            await client.get("/transactions", params={"limit": 0}),
            await client.get("/transactions", params={"limit": 101}),
            await client.get("/transactions", params={"offset": -1}),
            await client.get("/transactions", params={"status": "unknown"}),
        ]

    assert page_response.status_code == 200
    assert [
        transaction["provider_transaction_id"] for transaction in page_response.json()
    ] == ["provider-grocery"]
    assert all(response.status_code == 422 for response in invalid_responses)


@pytest.mark.anyio
async def test_transactions_returns_unavailable_when_database_query_fails() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.scalars.side_effect = OperationalError(
        "SELECT external_transactions",
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
        response = await client.get("/transactions")

    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}
