from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from persistence.models import TransactionSyncStateModel, UserModel

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class SyncStateDatabase:
    engine: Engine
    user_id: UUID


def _get_test_database_url() -> str:
    database_url = os.environ.get("LEDGE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("Set LEDGE_TEST_DATABASE_URL to run sync-state tests")

    database_name = make_url(database_url).database
    if database_name is None or not database_name.endswith("_test"):
        pytest.fail("Sync-state tests require a database ending in '_test'")

    return database_url


@pytest.fixture
def sync_state_database(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[SyncStateDatabase]:
    database_url = _get_test_database_url()
    monkeypatch.setenv("LEDGE_DATABASE_URL", database_url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))

    command.downgrade(config, "base")
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    user_id = uuid4()
    with Session(engine) as session, session.begin():
        session.add(UserModel(id=user_id))

    try:
        yield SyncStateDatabase(engine=engine, user_id=user_id)
    finally:
        engine.dispose()
        command.downgrade(config, "base")


@pytest.mark.integration
def test_sync_state_starts_without_cursor_and_persists_advancement(
    sync_state_database: SyncStateDatabase,
) -> None:
    sync_state_id = uuid4()

    with Session(sync_state_database.engine) as session, session.begin():
        session.add(
            TransactionSyncStateModel(
                id=sync_state_id,
                user_id=sync_state_database.user_id,
                provider_name="fake",
                provider_connection_id="connection-1",
                cursor=None,
            )
        )

    with Session(sync_state_database.engine) as session, session.begin():
        sync_state = session.get(TransactionSyncStateModel, sync_state_id)
        assert sync_state is not None
        assert sync_state.cursor is None
        assert sync_state.created_at is not None
        assert sync_state.updated_at is not None

        sync_state.cursor = "cursor-2"

    with Session(sync_state_database.engine) as session:
        sync_state = session.get(TransactionSyncStateModel, sync_state_id)
        assert sync_state is not None
        assert sync_state.cursor == "cursor-2"


@pytest.mark.integration
def test_provider_connection_can_belong_to_only_one_sync_state(
    sync_state_database: SyncStateDatabase,
) -> None:
    second_user_id = uuid4()

    with Session(sync_state_database.engine) as session, session.begin():
        session.add(UserModel(id=second_user_id))
        session.add(
            TransactionSyncStateModel(
                id=uuid4(),
                user_id=sync_state_database.user_id,
                provider_name="fake",
                provider_connection_id="connection-1",
                cursor=None,
            )
        )

    with (
        pytest.raises(IntegrityError, match="provider_connection"),
        Session(sync_state_database.engine) as session,
        session.begin(),
    ):
        session.add(
            TransactionSyncStateModel(
                id=uuid4(),
                user_id=second_user_id,
                provider_name="fake",
                provider_connection_id="connection-1",
                cursor=None,
            )
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("provider_name", "provider_connection_id"),
    [(" ", "connection-1"), ("fake", " ")],
)
def test_sync_state_rejects_blank_provider_identity(
    sync_state_database: SyncStateDatabase,
    provider_name: str,
    provider_connection_id: str,
) -> None:
    with (
        pytest.raises(IntegrityError, match="nonempty"),
        Session(sync_state_database.engine) as session,
        session.begin(),
    ):
        session.add(
            TransactionSyncStateModel(
                id=uuid4(),
                user_id=sync_state_database.user_id,
                provider_name=provider_name,
                provider_connection_id=provider_connection_id,
                cursor=None,
            )
        )


@pytest.mark.integration
def test_sync_state_rejects_unknown_user(
    sync_state_database: SyncStateDatabase,
) -> None:
    with (
        pytest.raises(IntegrityError, match="user_id"),
        Session(sync_state_database.engine) as session,
        session.begin(),
    ):
        session.add(
            TransactionSyncStateModel(
                id=uuid4(),
                user_id=uuid4(),
                provider_name="fake",
                provider_connection_id="connection-1",
                cursor=None,
            )
        )
