"""HTTP integrity policy helpers.

These helpers normalize route-specific integrity policies and keep shared
policy resolution outside concrete middleware classes.
"""

from __future__ import annotations

from dataclasses import dataclass

from starlette.types import ASGIApp

from star.core.errors import ErrorDef

IndexedContentTypePolicy = tuple[str, str, frozenset[str]]
IndexedBodyLimitPolicy = tuple[str, str, int, ErrorDef]


@dataclass(frozen=True, slots=True)
class ContentTypePolicy:
    """Policy defining allowed content types for a method/path pair.

    Attributes:
        method: HTTP method that triggers the policy.
        path: Request path that triggers the policy.
        allowed: Allowed base media types. Values are normalized during
            indexing.
    """

    method: str
    path: str
    allowed: frozenset[str]


@dataclass(frozen=True, slots=True)
class BodyLimitPolicy:
    """Policy defining a body size limit for a method/path pair.

    Attributes:
        method: HTTP method that triggers the policy.
        path: Request path that triggers the policy.
        max_bytes: Maximum number of accepted request body bytes.
        error: Public error returned when the limit is exceeded.
    """

    method: str
    path: str
    max_bytes: int
    error: ErrorDef


@dataclass(frozen=True, slots=True)
class ResolvedBodyLimit:
    """Resolved body limit and public error contract for one request.

    Attributes:
        max_bytes: Maximum number of accepted request body bytes.
        error: Public error returned when the request exceeds the limit.
    """

    max_bytes: int
    error: ErrorDef


def index_content_type_policies(
    policies: list[ContentTypePolicy],
) -> list[IndexedContentTypePolicy]:
    """Normalize content type policies for lookup.

    Args:
        policies: List of `ContentTypePolicy` instances.

    Returns:
        List of `(method, path, allowlist)` tuples.
    """

    indexed: list[IndexedContentTypePolicy] = []

    for policy in policies:
        indexed.append(
            (
                policy.method.upper(),
                policy.path,
                frozenset(content_type.lower() for content_type in policy.allowed),
            )
        )

    return indexed


def index_body_limit_policies(
    policies: list[BodyLimitPolicy],
) -> list[IndexedBodyLimitPolicy]:
    """Normalize body limit policies for lookup.

    Args:
        policies: List of `BodyLimitPolicy` instances.

    Returns:
        List of `(method, path, max_bytes, error)` tuples.
    """

    indexed: list[IndexedBodyLimitPolicy] = []

    for policy in policies:
        if policy.max_bytes <= 0:
            continue
        indexed.append(
            (policy.method.upper(), policy.path, policy.max_bytes, policy.error)
        )

    return indexed


def path_matches_policy_path(policy_path: str, request_path: str) -> bool:
    """Return True when a request path matches a configured policy path.

    A policy path can be either an exact literal path or a template path
    containing placeholders such as `/v1/actions/{action_id}`.

    Args:
        policy_path: Configured literal or template policy path.
        request_path: Incoming request path.

    Returns:
        True when the request path matches the policy path.
    """

    if policy_path == request_path:
        return True

    if "{" not in policy_path or "}" not in policy_path:
        return False

    policy_segments = policy_path.strip("/").split("/")
    request_segments = request_path.strip("/").split("/")
    if len(policy_segments) != len(request_segments):
        return False

    for policy_segment, request_segment in zip(
        policy_segments,
        request_segments,
        strict=True,
    ):
        if (
            policy_segment.startswith("{")
            and policy_segment.endswith("}")
            and len(policy_segment) > 2
        ):
            if not request_segment:
                return False
            continue
        if policy_segment != request_segment:
            return False

    return True


def resolve_content_type_policy(
    policies: list[IndexedContentTypePolicy],
    *,
    method: str,
    path: str,
) -> frozenset[str] | None:
    """Resolve the allowlist matching a method/path pair.

    Args:
        policies: Indexed content-type policies.
        method: Incoming HTTP method.
        path: Incoming request path.

    Returns:
        Matched content-type allowlist or None when no policy applies.
    """

    method_upper = method.upper()
    for policy_method, policy_path, allowed in policies:
        if policy_method != method_upper:
            continue
        if path_matches_policy_path(policy_path, path):
            return allowed

    return None


def resolve_body_limit_policy(
    policies: list[IndexedBodyLimitPolicy],
    *,
    default_limit: ResolvedBodyLimit,
    method: str,
    path: str,
) -> ResolvedBodyLimit:
    """Resolve the body limit policy matching a method/path pair.

    Args:
        policies: Indexed body-limit policies.
        default_limit: Limit returned when no route-specific policy matches.
        method: Incoming HTTP method.
        path: Incoming request path.

    Returns:
        Matched body limit or the default body limit.
    """

    method_upper = method.upper()
    for policy_method, policy_path, max_bytes, error in policies:
        if policy_method != method_upper:
            continue
        if path_matches_policy_path(policy_path, path):
            return ResolvedBodyLimit(max_bytes=max_bytes, error=error)

    return default_limit


def resolve_default_body_limit(app: ASGIApp, override: int | None) -> int:
    """Determine the allowed default body size limit.

    Args:
        app: ASGI application whose settings may contain `star_max_body_bytes`.
        override: Optional explicit override.

    Returns:
        Resolved byte limit, using a minimum safe fallback when unset.
    """

    if isinstance(override, int) and override > 0:
        return override

    settings = getattr(getattr(app, "state", None), "settings", None)
    configured = getattr(settings, "star_max_body_bytes", None)
    if isinstance(configured, int) and configured > 0:
        return configured

    return 1024 * 1024


__all__ = [
    "BodyLimitPolicy",
    "ContentTypePolicy",
    "IndexedBodyLimitPolicy",
    "IndexedContentTypePolicy",
    "ResolvedBodyLimit",
    "index_body_limit_policies",
    "index_content_type_policies",
    "path_matches_policy_path",
    "resolve_body_limit_policy",
    "resolve_content_type_policy",
    "resolve_default_body_limit",
]
