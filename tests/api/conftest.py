from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from httpx2 import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from api.app import create_app
from persistence.database import (
    create_async_database_engine,
    create_async_session_factory,
)
from persistence.models import FinancialAccountModel, UserModel

PROJECT_ROOT = Path(__file__).resolve().parents[2]
USER_ID = UUID("11111111-1111-1111-1111-111111111111")
OTHER_USER_ID = UUID("22222222-2222-2222-2222-222222222222")
EMPTY_USER_ID = UUID("33333333-3333-3333-3333-333333333333")
CHECKING_ACCOUNT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SAVINGS_ACCOUNT_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
OTHER_ACCOUNT_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


def _get_test_database_url() -> str:
    database_url = os.environ.get("LEDGE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("Set LEDGE_TEST_DATABASE_URL to run API integration tests")

    database_name = make_url(database_url).database
    if database_name is None or not database_name.endswith("_test"):
        pytest.fail("API integration tests require a database ending in '_test'")

    return database_url


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def api_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_url = _get_test_database_url()
    monkeypatch.setenv("LEDGE_DATABASE_URL", database_url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))

    command.downgrade(config, "base")
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    with Session(engine) as session, session.begin():
        session.add_all(
            [
                UserModel(id=USER_ID),
                UserModel(id=OTHER_USER_ID),
                UserModel(id=EMPTY_USER_ID),
            ]
        )
        session.add_all(
            [
                FinancialAccountModel(
                    id=CHECKING_ACCOUNT_ID,
                    user_id=USER_ID,
                    name="Everyday Checking",
                    account_type="checking",
                ),
                FinancialAccountModel(
                    id=SAVINGS_ACCOUNT_ID,
                    user_id=USER_ID,
                    name="Rainy Day Savings",
                    account_type="savings",
                ),
                FinancialAccountModel(
                    id=OTHER_ACCOUNT_ID,
                    user_id=OTHER_USER_ID,
                    name="Someone Else's Account",
                    account_type="credit",
                ),
            ]
        )
    engine.dispose()

    try:
        yield database_url
    finally:
        command.downgrade(config, "base")


@pytest.fixture
def api_client_factory(
    api_database: str,
) -> Callable[[UUID], AsyncIterator[AsyncClient]]:
    async def create_client(user_id: UUID = USER_ID) -> AsyncIterator[AsyncClient]:
        engine = create_async_database_engine(api_database)
        app = create_app(
            session_factory=create_async_session_factory(engine),
            user_id=user_id,
        )
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                yield client
        finally:
            await engine.dispose()

    return create_client
