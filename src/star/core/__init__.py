"""Core helpers and shared infrastructure for the STAR application.

This package exposes cross-cutting components used by the application,
for example exception handlers, metrics helpers and global logging
configuration.

Exports:
    error_json_response, generic_exception_handler, http_exception_handler,
    star_error_json_response
"""

from .config import Settings, get_settings
from .exceptions import generic_exception_handler, http_exception_handler
from .responses import error_json_response, star_error_json_response
from .schemas import envelope
from .security import paths

__all__ = [
    "Settings",
    "error_json_response",
    "get_settings",
    "paths",
    "envelope",
    "generic_exception_handler",
    "http_exception_handler",
    "star_error_json_response",
]
