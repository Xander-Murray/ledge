from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from api.accounts import router as accounts_router
from api.config import get_configured_user_id
from api.health import router as health_router
from api.sync_status import router as sync_status_router
from api.transactions import router as transactions_router
from persistence.database import (
    AsyncSessionFactory,
    create_async_database_engine,
    create_async_session_factory,
    get_database_url,
)


def create_app(
    *,
    session_factory: AsyncSessionFactory | None = None,
    user_id: UUID | None = None,
) -> FastAPI:
    """Build Ledge's HTTP application with explicit infrastructure wiring."""
    owned_engine: AsyncEngine | None = None
    if session_factory is None:
        owned_engine = create_async_database_engine(get_database_url())
        session_factory = create_async_session_factory(owned_engine)
    if user_id is None:
        user_id = get_configured_user_id()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if owned_engine is not None:
                await owned_engine.dispose()

    app = FastAPI(
        title="Ledge API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.session_factory = session_factory
    app.state.user_id = user_id
    app.include_router(health_router)
    app.include_router(accounts_router)
    app.include_router(transactions_router)
    app.include_router(sync_status_router)
    return app
