from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from httpx2 import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from api.app import create_app
from persistence.database import (
    AsyncSessionFactory,
    create_async_database_engine,
    create_async_session_factory,
)


def health_check_session_factory(*, available: bool = True) -> AsyncSessionFactory:
    session = AsyncMock(spec=AsyncSession)
    if available:
        session.scalar.return_value = 1
    else:
        session.scalar.side_effect = OperationalError(
            "SELECT 1",
            {},
            Exception("database is offline"),
        )

    @asynccontextmanager
    async def session_context() -> AsyncIterator[AsyncSession]:
        yield session

    return session_context


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_application_exposes_metadata_without_opening_a_database_connection() -> None:
    app = create_app(session_factory=health_check_session_factory())

    assert app.title == "Ledge API"
    assert app.version == "0.1.0"


@pytest.mark.anyio
async def test_health_reports_service_and_database_readiness() -> None:
    app = create_app(session_factory=health_check_session_factory())

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


@pytest.mark.anyio
async def test_health_returns_service_unavailable_when_database_query_fails() -> None:
    app = create_app(session_factory=health_check_session_factory(available=False))

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}


@pytest.mark.integration
@pytest.mark.anyio
async def test_health_checks_real_postgresql() -> None:
    database_url = os.environ.get("LEDGE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("Set LEDGE_TEST_DATABASE_URL to run API integration tests")
    database_name = make_url(database_url).database
    if database_name is None or not database_name.endswith("_test"):
        pytest.fail("API integration tests require a database ending in '_test'")

    engine = create_async_database_engine(database_url)
    app = create_app(session_factory=create_async_session_factory(engine))
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "database": "ok"}
    finally:
        await engine.dispose()
