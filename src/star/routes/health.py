"""Health route definitions for STAR."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from star.core.schemas.envelope import ResponseEnvelope

router = APIRouter(prefix="", tags=["health"])


class HealthResult(BaseModel):
    """Readiness payload returned by the health endpoint.

    Attributes:
        status: Health status value expected to be `ok`.
    """

    status: Literal["ok"]


@router.get("/health", response_model=ResponseEnvelope[HealthResult])
async def health() -> ResponseEnvelope[HealthResult]:
    """Health check endpoint.

    Returns a minimal readiness payload as defined in the SRS.

    Returns:
        A success envelope containing the readiness status.
    """

    return ResponseEnvelope.from_success(HealthResult(status="ok"))
