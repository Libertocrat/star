"""Prometheus metrics exposition route for STAR."""

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter(prefix="", tags=["metrics"])


@router.get("/metrics")
async def metrics() -> Response:
    """Prometheus metrics exposition endpoint.

    This endpoint returns the current Prometheus metrics snapshot for the
    running process using the `prometheus_client` library.

    Returns:
        A Starlette Response with the metrics payload and Prometheus content
        type (`CONTENT_TYPE_LATEST`).
    """

    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)
