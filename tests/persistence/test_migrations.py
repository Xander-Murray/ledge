from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _get_test_database_url() -> str:
    database_url = os.environ.get("LEDGE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("Set LEDGE_TEST_DATABASE_URL to run migration tests")

    database_name = make_url(database_url).database
    if database_name is None or not database_name.endswith("_test"):
        pytest.fail("Migration tests require a database whose name ends in '_test'")

    return database_url


@pytest.mark.integration
def test_migrations_upgrade_and_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _get_test_database_url()
    monkeypatch.setenv("LEDGE_DATABASE_URL", database_url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    engine = create_engine(database_url)

    command.downgrade(config, "base")
    try:
        command.upgrade(config, "head")

        assert set(inspect(engine).get_table_names()) == {
            "alembic_version",
            "external_transactions",
            "financial_accounts",
            "journal_entries",
            "postings",
            "users",
        }
        command.check(config)
    finally:
        engine.dispose()
        command.downgrade(config, "base")

    verification_engine = create_engine(database_url)
    try:
        remaining_tables = set(inspect(verification_engine).get_table_names())
        assert remaining_tables <= {"alembic_version"}
    finally:
        verification_engine.dispose()
