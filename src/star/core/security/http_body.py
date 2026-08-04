"""HTTP request-body helpers for structural integrity checks.

These helpers operate on ASGI request messages and keep reusable body policy
logic outside concrete middleware classes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from starlette.types import Message, Receive

BODY_ALLOWED_METHODS: Final[frozenset[str]] = frozenset({"POST", "PUT", "PATCH"})
BODY_NOT_ALLOWED_MESSAGE: Final[str] = "Request body is not allowed for this method"


@dataclass(frozen=True, slots=True)
class BodylessRequestResult:
    """Represent bodyless-method inspection state.

    Attributes:
        body_detected: Whether the request declares or carries a body.
        receive: Receive callable to pass downstream when no body was detected.
            This may replay one inspected message before delegating.
    """

    body_detected: bool
    receive: Receive


@dataclass(frozen=True, slots=True)
class BodyLimitReceive:
    """Represent a receive wrapper and its overflow predicate.

    Attributes:
        receive: ASGI receive callable that tracks streamed body bytes.
        limit_exceeded: Predicate returning whether the stream exceeded its
            configured maximum.
    """

    receive: Receive
    limit_exceeded: Callable[[], bool]


def method_allows_request_body(method: str) -> bool:
    """Return whether STAR treats an HTTP method as body-capable.

    Args:
        method: Incoming HTTP method.

    Returns:
        True when the method is allowed to carry a request body.
    """

    return method.upper() in BODY_ALLOWED_METHODS


def request_declares_body(
    *,
    declared_size: int | None,
    has_transfer_encoding: bool,
) -> bool:
    """Return whether headers declare that a request body is present.

    Args:
        declared_size: Parsed `Content-Length`, or None when absent.
        has_transfer_encoding: Whether `Transfer-Encoding` is present.

    Returns:
        True when headers indicate a non-empty or streaming request body.
    """

    return (declared_size is not None and declared_size > 0) or has_transfer_encoding


def request_message_has_body(message: Message) -> bool:
    """Return whether an ASGI request message carries body data.

    Args:
        message: ASGI message to inspect.

    Returns:
        True when the message contains body bytes or continues a body stream.
    """

    if message.get("type") != "http.request":
        return False
    return bool(message.get("body", b"")) or bool(message.get("more_body"))


def prepend_receive(first_message: Message, receive: Receive) -> Receive:
    """Return a receive callable that replays one already-read message.

    Args:
        first_message: Message already received from the original stream.
        receive: Original receive callable for remaining messages.

    Returns:
        Receive callable that yields `first_message` once before delegating.
    """

    replay_first = True

    async def replaying_receive() -> Message:
        """Replay the first message before delegating to the original receive."""

        nonlocal replay_first
        if replay_first:
            replay_first = False
            return first_message
        return await receive()

    return replaying_receive


async def inspect_bodyless_request(
    *,
    receive: Receive,
    declared_size: int | None,
    has_transfer_encoding: bool,
) -> BodylessRequestResult:
    """Inspect a request stream for a method that must not carry a body.

    Args:
        receive: Original ASGI receive callable.
        declared_size: Parsed `Content-Length`, or None when absent.
        has_transfer_encoding: Whether `Transfer-Encoding` is present.

    Returns:
        Inspection result with an optional replaying receive callable.
    """

    if request_declares_body(
        declared_size=declared_size,
        has_transfer_encoding=has_transfer_encoding,
    ):
        return BodylessRequestResult(body_detected=True, receive=receive)

    if declared_size is not None:
        return BodylessRequestResult(body_detected=False, receive=receive)

    first_message = await receive()
    if request_message_has_body(first_message):
        return BodylessRequestResult(body_detected=True, receive=receive)

    return BodylessRequestResult(
        body_detected=False,
        receive=prepend_receive(first_message, receive),
    )


def wrap_receive_with_body_limit(receive: Receive, max_bytes: int) -> BodyLimitReceive:
    """Wrap `receive` to enforce a total body byte limit.

    Args:
        receive: Original ASGI receive callable.
        max_bytes: Maximum number of allowed body bytes.

    Returns:
        Wrapped receive callable plus a predicate indicating whether the limit
        was exceeded.
    """

    total = 0
    exceeded = False

    async def limited_receive() -> Message:
        """Track streamed request bytes and disconnect after overflow."""

        nonlocal exceeded, total
        if exceeded:
            return {"type": "http.disconnect"}

        message = await receive()

        if message.get("type") != "http.request":
            return message

        body = message.get("body", b"")
        if body:
            total += len(body)
            if total > max_bytes:
                exceeded = True
                return {"type": "http.disconnect"}

        return message

    def limit_exceeded() -> bool:
        """Return whether the request stream exceeded its body limit."""

        return exceeded

    return BodyLimitReceive(receive=limited_receive, limit_exceeded=limit_exceeded)


__all__ = [
    "BODY_ALLOWED_METHODS",
    "BODY_NOT_ALLOWED_MESSAGE",
    "BodyLimitReceive",
    "BodylessRequestResult",
    "inspect_bodyless_request",
    "method_allows_request_body",
    "prepend_receive",
    "request_declares_body",
    "request_message_has_body",
    "wrap_receive_with_body_limit",
]
