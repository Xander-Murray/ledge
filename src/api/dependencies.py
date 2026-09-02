from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from persistence.database import AsyncSessionFactory


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Provide one database session for the lifetime of an HTTP request."""
    session_factory: AsyncSessionFactory = request.app.state.session_factory
    async with session_factory() as session:
        yield session
