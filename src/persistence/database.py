from __future__ import annotations

import os
from collections.abc import Mapping

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

DATABASE_URL_ENV_VAR = "LEDGE_DATABASE_URL"


class DatabaseConfigurationError(RuntimeError):
    """Raised when Ledge cannot determine how to connect to PostgreSQL."""


def get_database_url(environ: Mapping[str, str] | None = None) -> str:
    """Return the configured database URL."""
    source = os.environ if environ is None else environ
    database_url = source.get(DATABASE_URL_ENV_VAR)

    if database_url is None or not database_url.strip():
        raise DatabaseConfigurationError(f"{DATABASE_URL_ENV_VAR} is missing or empty")

    return database_url


def create_database_engine(database_url: str) -> Engine:
    """Create Ledge's SQLAlchemy engine without opening a connection."""
    engine = create_engine(database_url, pool_pre_ping=True)
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create sessions bound to engine."""
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return session_factory
