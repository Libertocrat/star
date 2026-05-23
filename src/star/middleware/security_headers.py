"""ASGI middleware that finalizes baseline response security headers."""

from __future__ import annotations

from typing import Final

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_REMOVE_HEADERS: Final[set[bytes]] = {
    b"server",
    b"x-powered-by",
}
_BASELINE_HEADERS: Final[list[tuple[bytes, bytes]]] = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (
        b"permissions-policy",
        b"accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
        b"magnetometer=(), microphone=(), payment=(), usb=()",
    ),
]
_BASELINE_HEADER_NAMES: Final[set[bytes]] = {name for name, _ in _BASELINE_HEADERS}


class SecurityHeadersMiddleware:
    """ASGI middleware that enforces baseline response security headers.

    For every HTTP response, the middleware removes fingerprinting headers and
    sets authoritative baseline security headers.

    Args:
        app: The ASGI application to wrap.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Store the wrapped ASGI application."""

        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process a request and rewrite response start headers when HTTP.

        Args:
            scope: ASGI scope.
            receive: ASGI receive callable.
            send: ASGI send callable.
        """

        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            """Rewrite response-start headers to enforce the baseline set."""

            if message.get("type") == "http.response.start":
                raw_headers = message.get("headers")
                headers: list[tuple[bytes, bytes]]
                if isinstance(raw_headers, list):
                    headers = raw_headers
                else:
                    headers = []

                new_headers: list[tuple[bytes, bytes]] = []
                for header_name, header_value in headers:
                    name_lc = header_name.lower()
                    if name_lc in _REMOVE_HEADERS or name_lc in _BASELINE_HEADER_NAMES:
                        continue
                    new_headers.append((header_name, header_value))

                new_headers.extend(_BASELINE_HEADERS)
                message["headers"] = new_headers

            await send(message)

        await self.app(scope, receive, send_wrapper)


__all__ = ["SecurityHeadersMiddleware"]
