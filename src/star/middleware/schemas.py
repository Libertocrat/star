"""Small schema types shared by STAR middleware components."""

from dataclasses import dataclass

from star.core.errors import ErrorDef


@dataclass(frozen=True)
class ContentTypePolicy:
    """Policy defining allowed content types for a method/path pair.

    Attributes:
        method: HTTP method that triggers the policy.
        path: Request path that triggers the policy.
        allowed: Allowed base media types as lowercase strings.
    """

    method: str
    path: str
    allowed: frozenset[str]


@dataclass(frozen=True)
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
