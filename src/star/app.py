"""Application factory for the Secure Templated Actions Runtime (STAR).

This module exposes `create_app()` which constructs and configures the
FastAPI application used by the service. The ASGI application instance is
exported as `app` for use by ASGI servers (for example, uvicorn).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException

from star.actions.registry import build_registry_from_specs
from star.core import (
    Settings,
    generic_exception_handler,
    get_settings,
    http_exception_handler,
)
from star.core.openapi import build_openapi_schema
from star.core.utils.file_storage import ensure_storage_dirs
from star.middleware.auth import AuthMiddleware
from star.middleware.observability import ObservabilityMiddleware
from star.middleware.rate_limit import RateLimitMiddleware
from star.middleware.request_id import RequestIDMiddleware
from star.middleware.request_integrity import RequestIntegrityMiddleware
from star.middleware.schemas import ContentTypePolicy
from star.middleware.security_headers import SecurityHeadersMiddleware
from star.middleware.timeout import TimeoutMiddleware
from star.routes.actions.router import router as execute_router
from star.routes.files.router import router as files_router
from star.routes.health import router as health_router
from star.routes.metrics import router as metrics_router


class STARApp(FastAPI):
    """FastAPI subclass that centralizes STAR OpenAPI customization.

    Why this wrapper exists:
        Replacing `app.openapi` with a lambda/closure works at runtime, but it
        weakens static analysis and makes override intent less explicit.
        A dedicated subclass keeps the behavior discoverable, type-checker
        friendly, and close to FastAPI's extension model.
    """

    def openapi(self) -> dict[str, Any]:
        """Build and cache the custom OpenAPI document for this app instance.

        Returns:
            The cached OpenAPI schema, building it once when needed.
        """

        # Keep FastAPI's lazy-cache behavior so schema generation remains cheap
        # after the first request while still allowing a dynamic first build.
        if self.openapi_schema:
            return self.openapi_schema

        schema = build_openapi_schema(self)
        self.openapi_schema = schema
        return schema


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application instance.

    This function centralizes application construction so tests can instantiate
    a configured app with alternate settings. It performs the following
    responsibilities:

    - Instantiate application settings (Pydantic `Settings`).
    - Register middleware in the required order.
    - Register global exception handlers.
    - Include API routers.

    Args:
        settings: Optional pre-constructed Settings object for tests.

    Returns:
        A configured `FastAPI` application instance.
    """

    settings = settings or get_settings()
    ensure_storage_dirs(settings)

    # Interactive documentation endpoints are opt-in and should remain disabled
    # in production; expose them only when `star_enable_docs=True`.
    docs_url = "/docs" if settings.star_enable_docs else None
    redoc_url = "/redoc" if settings.star_enable_docs else None
    openapi_url = "/openapi.json" if settings.star_enable_docs else None

    app = STARApp(
        title="Secure Templated Actions Runtime (STAR)",
        version=settings.star_app_version,
        description=(
            "Secure Templated Actions Runtime (STAR) is a hardened execution "
            "microservice designed to expose a strictly allow-listed action "
            "surface for automation platforms and internal systems.\n\n"
            "It enforces:\n"
            "- Explicit action registry (no arbitrary command execution)\n"
            "- Defense-in-depth middleware stack\n"
            "- Deterministic response envelopes\n"
            "- Centralized error taxonomy\n"
            "- Observability-first architecture\n"
            "- Runtime-aware OpenAPI contract generation"
        ),
        contact={
            "name": "Libertocrat",
            "url": "https://github.com/Libertocrat/",
            "email": "libertocrat@proton.me",
        },
        license_info={
            "name": "Apache License 2.0",
            "url": "https://www.apache.org/licenses/LICENSE-2.0.html",
        },
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        swagger_ui_parameters={
            "docExpansion": "list",
            "defaultModelsExpandDepth": 0,
            "defaultModelExpandDepth": 2,
            "displayRequestDuration": True,
            "filter": True,
            "persistAuthorization": True,
            "deepLinking": True,
            "showExtensions": True,
            "showCommonExtensions": True,
        },
    )

    # Attach settings to app state (single source of truth)
    app.state.settings = settings

    try:
        app.state.action_registry = build_registry_from_specs(settings)
    except Exception as exc:
        raise RuntimeError("Failed to build action registry") from exc

    # Middlewares (order matters): registration is written so runtime order
    # becomes SecurityHeaders (optional) -> RequestID -> Observability ->
    # RateLimit -> Timeout -> RequestIntegrity -> Auth -> Router
    # (Starlette runs last-added middleware first).
    app.add_middleware(AuthMiddleware, api_token=settings.star_api_token)
    app.add_middleware(
        RequestIntegrityMiddleware,
        max_body_bytes=settings.star_max_file_bytes,
        content_type_policies=[
            ContentTypePolicy(
                method="POST",
                path="/v1/actions/{action_id}",
                allowed=frozenset({"application/json"}),
            ),
            ContentTypePolicy(
                method="POST",
                path="/v1/files",
                allowed=frozenset({"multipart/form-data"}),
            ),
        ],
    )
    app.add_middleware(TimeoutMiddleware, timeout_ms=settings.star_timeout_ms)
    app.add_middleware(RateLimitMiddleware, rate_limit_rps=settings.star_rate_limit_rps)
    app.add_middleware(ObservabilityMiddleware)
    app.add_middleware(RequestIDMiddleware)
    if settings.star_enable_security_headers:
        app.add_middleware(SecurityHeadersMiddleware)

    # Fallback exception handlers
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    # Routers
    app.include_router(health_router)
    app.include_router(metrics_router)
    app.include_router(execute_router)
    app.include_router(files_router)

    return app
