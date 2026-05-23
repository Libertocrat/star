"""HTTP helpers shared by STAR middleware and services.

This module provides canonical helpers for normalizing request paths and
classifying HTTP status codes, keeping metric labels stable across
middleware and services.
"""

from __future__ import annotations


def normalize_metric_path(path: str) -> str:
    """Normalize a request path for low-cardinality metric labels.

    Args:
        path: Original request path.

    Returns:
        Canonicalized path without query parameters and trailing slash, except
        root path.
    """

    clean_path = path.split("?", 1)[0].rstrip("/")
    return clean_path or "/"


def status_class_from_code(status_code: int | None) -> str:
    """Classify an HTTP status code into a broad status class.

    Args:
        status_code: Captured HTTP status code.

    Returns:
        One of "2xx", "3xx", "4xx", "5xx", or "unknown".
    """

    if status_code is None:
        return "unknown"
    if 200 <= status_code <= 299:
        return "2xx"
    if 300 <= status_code <= 399:
        return "3xx"
    if 400 <= status_code <= 499:
        return "4xx"
    if 500 <= status_code <= 599:
        return "5xx"
    return "unknown"
