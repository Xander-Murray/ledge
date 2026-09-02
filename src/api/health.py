from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_session

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Health of the HTTP service and its required database dependency."""

    status: Literal["ok"] = "ok"
    database: Literal["ok"] = "ok"


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
)
async def get_health(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HealthResponse:
    """Report readiness only when PostgreSQL accepts a simple query."""
    try:
        result = await session.scalar(text("SELECT 1"))
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        ) from error

    if result != 1:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database health check returned an unexpected result",
        )

    return HealthResponse()
