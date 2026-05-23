"""
Secure Templated Actions Runtime (STAR) package.

This package exposes the application factory and shared components.
The ASGI application is intentionally NOT instantiated at import time
to avoid configuration side-effects.
"""

from . import actions, core, middleware, routes
from .app import create_app
from .core import config

__all__ = ["core", "config", "actions", "middleware", "routes", "create_app"]
