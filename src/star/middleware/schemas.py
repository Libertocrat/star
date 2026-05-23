"""Small schema types shared by STAR middleware components."""

from dataclasses import dataclass


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
