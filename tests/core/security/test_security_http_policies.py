"""Tests for HTTP integrity policy helpers.

These tests freeze reusable policy matching and body-limit resolution without
assembling the middleware stack.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from star.core.errors import FILE_TOO_LARGE, REQUEST_BODY_TOO_LARGE
from star.core.security.http_policies import (
    BodyLimitPolicy,
    ContentTypePolicy,
    ResolvedBodyLimit,
    index_body_limit_policies,
    index_content_type_policies,
    path_matches_policy_path,
    resolve_body_limit_policy,
    resolve_content_type_policy,
    resolve_default_body_limit,
)

# ============================================================================
# Helpers
# ============================================================================


class _SettingsBackedApp:
    """No-op ASGI app carrying settings on state.

    Attributes:
        state: Application state namespace containing a settings object.
    """

    def __init__(self, limit: int | None) -> None:
        """Initialize state with an optional body limit.

        Args:
            limit: Optional `star_max_body_bytes` setting value.
        """

        self.state = SimpleNamespace(
            settings=SimpleNamespace(star_max_body_bytes=limit)
        )

    async def __call__(self, _scope, _receive, _send) -> None:
        """Accept an ASGI call without sending a response."""


def _app_with_body_limit(limit: int | None) -> _SettingsBackedApp:
    """Return an ASGI app carrying an optional settings body limit.

    Args:
        limit: Optional `star_max_body_bytes` setting value.

    Returns:
        ASGI app object with a mutable `state.settings` attribute.
    """

    return _SettingsBackedApp(limit)


# ============================================================================
# Path Matching
# ============================================================================


@pytest.mark.parametrize(
    ("policy_path", "request_path", "expected"),
    [
        pytest.param("/v1/files", "/v1/files", True, id="literal-match"),
        pytest.param("/v1/files", "/v1/files/extra", False, id="literal-mismatch"),
        pytest.param(
            "/v1/actions/{action_id}",
            "/v1/actions/noop",
            True,
            id="template-match",
        ),
        pytest.param(
            "/v1/actions/{action_id}",
            "/v1/actions",
            False,
            id="template-segment-missing",
        ),
        pytest.param(
            "/v1/files/{file_id}/content",
            "/v1/files/abc/content",
            True,
            id="template-middle-segment-match",
        ),
        pytest.param(
            "/v1/files/{file_id}/content",
            "/v1/files/abc/meta",
            False,
            id="template-literal-tail-mismatch",
        ),
        pytest.param("/v1/files/{}", "/v1/files/abc", False, id="empty-token"),
    ],
)
def test_path_matches_policy_path(policy_path, request_path, expected):
    """
    GIVEN a configured literal or template policy path
    WHEN a request path is matched against it
    THEN only exact segment-compatible paths match
    """
    matches = path_matches_policy_path(policy_path, request_path)

    assert matches is expected


# ============================================================================
# Content-Type Policies
# ============================================================================


def test_resolve_content_type_policy_normalizes_method_and_media_types():
    """
    GIVEN a content-type policy with mixed-case method and media type
    WHEN a matching request path is resolved
    THEN the normalized allowlist is returned
    """
    policies = index_content_type_policies(
        [
            ContentTypePolicy(
                method="post",
                path="/v1/actions/{action_id}",
                allowed=frozenset({"Application/JSON"}),
            )
        ]
    )

    resolved = resolve_content_type_policy(
        policies,
        method="POST",
        path="/v1/actions/noop",
    )

    assert resolved == frozenset({"application/json"})


def test_resolve_content_type_policy_returns_none_for_unmatched_request():
    """
    GIVEN a content-type policy for POST action execution
    WHEN a GET request for the same path shape is resolved
    THEN no content-type policy is returned
    """
    policies = index_content_type_policies(
        [
            ContentTypePolicy(
                method="POST",
                path="/v1/actions/{action_id}",
                allowed=frozenset({"application/json"}),
            )
        ]
    )

    resolved = resolve_content_type_policy(
        policies,
        method="GET",
        path="/v1/actions/noop",
    )

    assert resolved is None


# ============================================================================
# Body Limit Policies
# ============================================================================


def test_index_body_limit_policies_ignores_non_positive_limits():
    """
    GIVEN body-limit policies include invalid non-positive limits
    WHEN policies are indexed
    THEN only positive limits remain available for resolution
    """
    policies = index_body_limit_policies(
        [
            BodyLimitPolicy("POST", "/zero", 0, FILE_TOO_LARGE),
            BodyLimitPolicy("POST", "/negative", -1, FILE_TOO_LARGE),
            BodyLimitPolicy("post", "/v1/files", 100, FILE_TOO_LARGE),
        ]
    )

    assert policies == [("POST", "/v1/files", 100, FILE_TOO_LARGE)]


def test_resolve_body_limit_policy_uses_upload_specific_limit():
    """
    GIVEN default and upload-specific body-limit policies
    WHEN POST /v1/files is resolved
    THEN the upload-specific limit and error contract are returned
    """
    default_limit = ResolvedBodyLimit(
        max_bytes=16,
        error=REQUEST_BODY_TOO_LARGE,
    )
    policies = index_body_limit_policies(
        [BodyLimitPolicy("post", "/v1/files", 100, FILE_TOO_LARGE)]
    )

    resolved = resolve_body_limit_policy(
        policies,
        default_limit=default_limit,
        method="POST",
        path="/v1/files",
    )

    assert resolved.max_bytes == 100
    assert resolved.error is FILE_TOO_LARGE


def test_resolve_body_limit_policy_returns_default_for_unmatched_request():
    """
    GIVEN default and upload-specific body-limit policies
    WHEN an action execution request is resolved
    THEN the default body limit and error contract are returned
    """
    default_limit = ResolvedBodyLimit(
        max_bytes=16,
        error=REQUEST_BODY_TOO_LARGE,
    )
    policies = index_body_limit_policies(
        [BodyLimitPolicy("POST", "/v1/files", 100, FILE_TOO_LARGE)]
    )

    resolved = resolve_body_limit_policy(
        policies,
        default_limit=default_limit,
        method="POST",
        path="/v1/actions/noop",
    )

    assert resolved is default_limit


# ============================================================================
# Default Body Limit
# ============================================================================


def test_resolve_default_body_limit_prefers_positive_override():
    """
    GIVEN an explicit positive body-limit override and app settings
    WHEN the default body limit is resolved
    THEN the explicit override wins
    """
    app = _app_with_body_limit(2048)

    resolved = resolve_default_body_limit(app, 512)

    assert resolved == 512


def test_resolve_default_body_limit_uses_app_settings_when_override_absent():
    """
    GIVEN no explicit body-limit override and app settings contain a limit
    WHEN the default body limit is resolved
    THEN the settings value is returned
    """
    app = _app_with_body_limit(2048)

    resolved = resolve_default_body_limit(app, None)

    assert resolved == 2048


@pytest.mark.parametrize(
    "configured_limit",
    [
        pytest.param(None, id="missing-setting"),
        pytest.param(0, id="zero-setting"),
        pytest.param(-1, id="negative-setting"),
    ],
)
def test_resolve_default_body_limit_uses_safe_fallback(configured_limit):
    """
    GIVEN no positive explicit override or app setting
    WHEN the default body limit is resolved
    THEN the safe fallback limit is returned
    """
    app = _app_with_body_limit(configured_limit)

    resolved = resolve_default_body_limit(app, None)

    assert resolved == 1024 * 1024
