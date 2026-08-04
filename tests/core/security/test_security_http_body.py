"""Tests for HTTP request-body integrity helpers.

These tests freeze reusable ASGI body policy behavior without assembling the
full middleware stack.
"""

from __future__ import annotations

import pytest
from starlette.types import Message, Receive

from star.core.security.http_body import (
    inspect_bodyless_request,
    method_allows_request_body,
    request_declares_body,
    request_message_has_body,
    wrap_receive_with_body_limit,
)

# ============================================================================
# Helpers
# ============================================================================


def _receive_from(messages: list[Message]) -> Receive:
    """Return a receive callable backed by a fixed message sequence.

    Args:
        messages: Messages returned before a default disconnect.

    Returns:
        ASGI receive callable for deterministic helper tests.
    """

    pending = list(messages)

    async def receive() -> Message:
        """Return the next queued message or a deterministic disconnect."""

        if pending:
            return pending.pop(0)
        return {"type": "http.disconnect"}

    return receive


# ============================================================================
# Body Policy
# ============================================================================


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        pytest.param("POST", True, id="post-allows-body"),
        pytest.param("put", True, id="put-normalized"),
        pytest.param("PATCH", True, id="patch-allows-body"),
        pytest.param("GET", False, id="get-rejects-body"),
        pytest.param("DELETE", False, id="delete-rejects-body"),
        pytest.param("OPTIONS", False, id="options-rejects-body"),
    ],
)
def test_method_allows_request_body(method, expected):
    """
    GIVEN an HTTP method
    WHEN STAR body-capable method policy is evaluated
    THEN only POST, PUT, and PATCH are allowed to carry request bodies
    """
    allowed = method_allows_request_body(method)

    assert allowed is expected


@pytest.mark.parametrize(
    ("declared_size", "has_transfer_encoding", "expected"),
    [
        pytest.param(None, False, False, id="no-framing-no-body"),
        pytest.param(0, False, False, id="zero-content-length-no-body"),
        pytest.param(1, False, True, id="positive-content-length-body"),
        pytest.param(None, True, True, id="transfer-encoding-body"),
        pytest.param(0, True, True, id="transfer-encoding-wins"),
    ],
)
def test_request_declares_body(declared_size, has_transfer_encoding, expected):
    """
    GIVEN parsed body framing headers
    WHEN body declaration policy is evaluated
    THEN non-empty Content-Length or Transfer-Encoding declares a body
    """
    declares_body = request_declares_body(
        declared_size=declared_size,
        has_transfer_encoding=has_transfer_encoding,
    )

    assert declares_body is expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        pytest.param(
            {"type": "http.request", "body": b"payload", "more_body": False},
            True,
            id="body-bytes-detected",
        ),
        pytest.param(
            {"type": "http.request", "body": b"", "more_body": True},
            True,
            id="continued-stream-detected",
        ),
        pytest.param(
            {"type": "http.request", "body": b"", "more_body": False},
            False,
            id="empty-final-request-message",
        ),
        pytest.param(
            {"type": "http.disconnect"},
            False,
            id="disconnect-has-no-body",
        ),
    ],
)
def test_request_message_has_body(message, expected):
    """
    GIVEN an ASGI message
    WHEN request body presence is inspected
    THEN body bytes or more_body indicate request body content
    """
    has_body = request_message_has_body(message)

    assert has_body is expected


# ============================================================================
# Bodyless Request Inspection
# ============================================================================


@pytest.mark.asyncio
async def test_inspect_bodyless_request_rejects_declared_content_length():
    """
    GIVEN a bodyless method request declares a non-empty Content-Length
    WHEN the request is inspected
    THEN body detection is reported without consuming the receive stream
    """
    receive = _receive_from([{"type": "http.request", "body": b"", "more_body": False}])

    result = await inspect_bodyless_request(
        receive=receive,
        declared_size=1,
        has_transfer_encoding=False,
    )

    assert result.body_detected is True
    assert result.receive is receive


@pytest.mark.asyncio
async def test_inspect_bodyless_request_rejects_transfer_encoding():
    """
    GIVEN a bodyless method request carries Transfer-Encoding
    WHEN the request is inspected
    THEN body detection is reported without consuming the receive stream
    """
    receive = _receive_from([{"type": "http.request", "body": b"", "more_body": False}])

    result = await inspect_bodyless_request(
        receive=receive,
        declared_size=None,
        has_transfer_encoding=True,
    )

    assert result.body_detected is True
    assert result.receive is receive


@pytest.mark.asyncio
async def test_inspect_bodyless_request_allows_zero_content_length():
    """
    GIVEN a bodyless method request declares Content-Length zero
    WHEN the request is inspected
    THEN no body is detected and the original receive callable is preserved
    """
    first_message = {"type": "http.request", "body": b"", "more_body": False}
    receive = _receive_from([first_message])

    result = await inspect_bodyless_request(
        receive=receive,
        declared_size=0,
        has_transfer_encoding=False,
    )

    assert result.body_detected is False
    assert result.receive is receive
    assert await result.receive() == first_message


@pytest.mark.asyncio
async def test_inspect_bodyless_request_rejects_first_body_chunk():
    """
    GIVEN a bodyless method request has no Content-Length
    WHEN the first streamed ASGI request message contains bytes
    THEN body detection is reported
    """
    receive = _receive_from(
        [{"type": "http.request", "body": b"payload", "more_body": False}]
    )

    result = await inspect_bodyless_request(
        receive=receive,
        declared_size=None,
        has_transfer_encoding=False,
    )

    assert result.body_detected is True


@pytest.mark.asyncio
async def test_inspect_bodyless_request_rejects_continued_empty_stream():
    """
    GIVEN a bodyless method request has no Content-Length
    WHEN the first ASGI request message continues the body stream
    THEN body detection is reported even when the first chunk is empty
    """
    receive = _receive_from([{"type": "http.request", "body": b"", "more_body": True}])

    result = await inspect_bodyless_request(
        receive=receive,
        declared_size=None,
        has_transfer_encoding=False,
    )

    assert result.body_detected is True


@pytest.mark.asyncio
async def test_inspect_bodyless_request_replays_empty_first_message():
    """
    GIVEN a bodyless method request has no Content-Length and no body
    WHEN the first ASGI request message is inspected
    THEN the returned receive callable replays it for downstream consumers
    """
    first_message = {"type": "http.request", "body": b"", "more_body": False}
    second_message = {"type": "http.disconnect"}
    receive = _receive_from([first_message, second_message])

    result = await inspect_bodyless_request(
        receive=receive,
        declared_size=None,
        has_transfer_encoding=False,
    )

    assert result.body_detected is False
    assert await result.receive() == first_message
    assert await result.receive() == second_message


# ============================================================================
# Streaming Body Limit
# ============================================================================


@pytest.mark.asyncio
async def test_wrap_receive_with_body_limit_allows_exact_limit():
    """
    GIVEN a streamed request body totals exactly the configured limit
    WHEN the wrapped receive callable reads all chunks
    THEN the original request messages pass through without overflow
    """
    first_message = {"type": "http.request", "body": b"12", "more_body": True}
    second_message = {"type": "http.request", "body": b"34", "more_body": False}
    limited = wrap_receive_with_body_limit(
        _receive_from([first_message, second_message]),
        4,
    )

    assert await limited.receive() == first_message
    assert limited.limit_exceeded() is False
    assert await limited.receive() == second_message
    assert limited.limit_exceeded() is False


@pytest.mark.asyncio
async def test_wrap_receive_with_body_limit_disconnects_after_overflow():
    """
    GIVEN a streamed request body exceeds the configured limit
    WHEN the overflowing chunk is read
    THEN the wrapper marks overflow and returns http.disconnect thereafter
    """
    limited = wrap_receive_with_body_limit(
        _receive_from(
            [
                {"type": "http.request", "body": b"123", "more_body": True},
                {"type": "http.request", "body": b"45", "more_body": False},
            ]
        ),
        4,
    )

    assert await limited.receive() == {
        "type": "http.request",
        "body": b"123",
        "more_body": True,
    }
    assert limited.limit_exceeded() is False
    assert await limited.receive() == {"type": "http.disconnect"}
    assert limited.limit_exceeded() is True
    assert await limited.receive() == {"type": "http.disconnect"}


@pytest.mark.asyncio
async def test_wrap_receive_with_body_limit_preserves_non_request_messages():
    """
    GIVEN the underlying receive callable returns a non-request ASGI message
    WHEN the body-limit wrapper reads it
    THEN the message is passed through and no overflow is marked
    """
    disconnect = {"type": "http.disconnect"}
    limited = wrap_receive_with_body_limit(_receive_from([disconnect]), 1)

    assert await limited.receive() == disconnect
    assert limited.limit_exceeded() is False
