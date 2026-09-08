from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from application.plaid_connection import (
    PlaidConnectionExistsError,
    PlaidSandboxConnector,
    load_plaid_account_ids,
)
from persistence.database import create_session_factory
from persistence.models import (
    FinancialAccountModel,
    ProviderAccountMappingModel,
    TransactionSyncStateModel,
)
from providers.plaid_sandbox import PlaidAccount, PlaidSandboxConnection

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class PlaidConnectionDatabase:
    engine: Engine
    user_id: UUID


class StubPlaidSandboxClient:
    def __init__(self) -> None:
        self.calls = 0

    def create_connection(self, *, institution_id: str) -> PlaidSandboxConnection:
        self.calls += 1
        assert institution_id == "ins-test"
        return PlaidSandboxConnection(
            access_token="access-sandbox-token",
            item_id="item-sandbox-1",
            accounts=(
                PlaidAccount("plaid-checking", "Checking", "depository", "checking"),
                PlaidAccount("plaid-savings", "Savings", "depository", "savings"),
                PlaidAccount("plaid-card", "Credit Card", "credit", "credit card"),
                PlaidAccount("plaid-loan", "Loan", "loan", "student"),
            ),
        )


def _get_test_database_url() -> str:
    database_url = os.environ.get("LEDGE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("Set LEDGE_TEST_DATABASE_URL to run Plaid connection tests")
    database_name = make_url(database_url).database
    if database_name is None or not database_name.endswith("_test"):
        pytest.fail("Plaid connection tests require a database ending in '_test'")
    return database_url


@pytest.fixture
def plaid_connection_database(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[PlaidConnectionDatabase]:
    database_url = _get_test_database_url()
    monkeypatch.setenv("LEDGE_DATABASE_URL", database_url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        yield PlaidConnectionDatabase(engine=engine, user_id=uuid4())
    finally:
        engine.dispose()
        command.downgrade(config, "base")


@pytest.mark.integration
def test_connect_persists_supported_accounts_and_loadable_mappings(
    plaid_connection_database: PlaidConnectionDatabase,
) -> None:
    plaid = StubPlaidSandboxClient()
    connector = PlaidSandboxConnector(
        session_factory=create_session_factory(plaid_connection_database.engine),
        plaid=plaid,
    )

    result = connector.connect(
        user_id=plaid_connection_database.user_id,
        institution_id="ins-test",
    )

    assert result.item_id == "item-sandbox-1"
    assert result.access_token == "access-sandbox-token"
    assert result.imported_account_count == 3
    assert result.skipped_account_count == 1
    with Session(plaid_connection_database.engine) as session:
        sync_state = session.get(TransactionSyncStateModel, result.sync_state_id)
        assert sync_state is not None
        assert sync_state.provider_connection_id == "item-sandbox-1"
        assert sync_state.cursor is None
        assert session.scalar(select(func.count(FinancialAccountModel.id))) == 3
        assert session.scalar(select(func.count(ProviderAccountMappingModel.id))) == 3
        mapping = load_plaid_account_ids(
            session,
            user_id=plaid_connection_database.user_id,
            item_id="item-sandbox-1",
        )
        assert set(mapping) == {"plaid-checking", "plaid-savings", "plaid-card"}
        assert all(isinstance(account_id, UUID) for account_id in mapping.values())

    with pytest.raises(PlaidConnectionExistsError):
        connector.connect(
            user_id=plaid_connection_database.user_id,
            institution_id="ins-test",
        )
    assert plaid.calls == 1
