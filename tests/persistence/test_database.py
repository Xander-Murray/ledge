from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from persistence.database import (
    DatabaseConfigurationError,
    create_database_engine,
    create_session_factory,
    get_database_url,
)


def test_get_database_url_reads_the_explicit_environment() -> None:
    expected = "postgresql+psycopg://ledge:secret@database.example/ledge"

    assert get_database_url({"LEDGE_DATABASE_URL": expected}) == expected


@pytest.mark.parametrize("environ", [{}, {"LEDGE_DATABASE_URL": "   "}])
def test_get_database_url_rejects_missing_or_blank_values(
    environ: dict[str, str],
) -> None:
    with pytest.raises(DatabaseConfigurationError, match="LEDGE_DATABASE_URL"):
        get_database_url(environ)


def test_create_database_engine_configures_postgresql_and_connection_checks() -> None:
    engine = create_database_engine(
        "postgresql+psycopg://ledge:secret@database.example/ledge"
    )

    try:
        assert engine.dialect.name == "postgresql"
        assert engine.pool._pre_ping is True
    finally:
        engine.dispose()


def test_create_session_factory_binds_sessions_without_expiring_on_commit() -> None:
    engine = create_database_engine(
        "postgresql+psycopg://ledge:secret@database.example/ledge"
    )
    session_factory = create_session_factory(engine)

    try:
        with session_factory() as session:
            assert session.get_bind() is engine
            assert session.expire_on_commit is False
    finally:
        engine.dispose()


@pytest.mark.integration
def test_postgresql_accepts_a_connection() -> None:
    database_url = os.environ.get("LEDGE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("Set LEDGE_TEST_DATABASE_URL to run PostgreSQL integration tests")

    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT 1")) == 1
    finally:
        engine.dispose()
