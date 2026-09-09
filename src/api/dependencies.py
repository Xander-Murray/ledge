from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from application.event_queue import EventPublisher
from persistence.database import AsyncSessionFactory


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Provide one database session for the lifetime of an HTTP request."""
    session_factory: AsyncSessionFactory = request.app.state.session_factory
    async with session_factory() as session:
        yield session


async def get_current_user_id(request: Request) -> UUID:
    """Return the identity configured for this single-user API instance."""
    user_id: UUID = request.app.state.user_id
    return user_id


async def get_event_publisher(request: Request) -> EventPublisher | None:
    """Return the configured asynchronous event publisher, when enabled."""
    publisher: EventPublisher | None = request.app.state.event_publisher
    return publisher
