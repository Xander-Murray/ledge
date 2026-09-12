from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from httpx2 import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from api.app import create_app
from application.event_queue import EventPublisher
from persistence.database import (
    AsyncSessionFactory,
    create_async_database_engine,
    create_async_session_factory,
)

USER_ID = UUID("11111111-1111-1111-1111-111111111111")


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
    app = create_app(
        session_factory=health_check_session_factory(),
        user_id=USER_ID,
    )

    assert app.title == "Ledge API"
    assert app.version == "0.1.0"


def test_application_builds_sqs_publisher_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue_url = "https://sqs.us-east-1.amazonaws.com/123/ledge-events"
    publisher = AsyncMock(spec=EventPublisher)
    monkeypatch.setenv("LEDGE_EVENT_QUEUE_URL", queue_url)

    with patch(
        "api.app.create_sqs_event_publisher",
        return_value=publisher,
    ) as publisher_factory:
        app = create_app(
            session_factory=health_check_session_factory(),
            user_id=USER_ID,
        )

    assert app.state.event_publisher is publisher
    publisher_factory.assert_called_once_with(queue_url=queue_url)


def test_explicit_publisher_takes_precedence_over_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = AsyncMock(spec=EventPublisher)
    monkeypatch.setenv(
        "LEDGE_EVENT_QUEUE_URL",
        "https://sqs.us-east-1.amazonaws.com/123/other-queue",
    )

    with patch("api.app.create_sqs_event_publisher") as publisher_factory:
        app = create_app(
            session_factory=health_check_session_factory(),
            user_id=USER_ID,
            event_publisher=publisher,
        )

    assert app.state.event_publisher is publisher
    publisher_factory.assert_not_called()


@pytest.mark.anyio
async def test_health_reports_service_and_database_readiness() -> None:
    app = create_app(
        session_factory=health_check_session_factory(),
        user_id=USER_ID,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


@pytest.mark.anyio
async def test_health_returns_service_unavailable_when_database_query_fails() -> None:
    app = create_app(
        session_factory=health_check_session_factory(available=False),
        user_id=USER_ID,
    )

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
    app = create_app(
        session_factory=create_async_session_factory(engine),
        user_id=USER_ID,
    )
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
