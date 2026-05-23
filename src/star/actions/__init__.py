# src/star/actions/__init__.py
"""Action package discovery helpers for STAR startup registration."""

from __future__ import annotations

import importlib
import logging
import pkgutil
from types import ModuleType

logger = logging.getLogger("star.actions")


def _import_module(name: str) -> ModuleType | None:
    """Import an action module and log failures without aborting discovery."""

    try:
        mod = importlib.import_module(name)
        return mod
    except Exception:
        logger.exception("Failed to import action module %s", name)
        return None


def discover_and_register(package_name: str = "star.actions") -> None:
    """Recursively discover and import modules under `package_name`.

    The standard `pkgutil.walk_packages` may list package names without
    importing their submodules. We implement an explicit stack-based
    recursion: import each package/module and, when a package is found,
    iterate its `__path__` to discover nested modules. Each imported
    module is expected to perform registration side-effects (e.g.
    calling `register_action`). Exceptions are logged and do not abort
    discovery to avoid a single broken action preventing startup.
    """

    root_pkg = importlib.import_module(package_name)
    # Log package path for diagnostics; this helps detect cases where the
    # package exists but its filesystem path isn't discoverable at runtime.
    try:
        # Access __path__ to ensure package is importable; do not log
        # the path here to avoid leaking filesystem details in startup
        # output used by deployment tooling.
        _ = getattr(root_pkg, "__path__", None)
    except Exception:
        logger.exception("Failed to read package __path__ for %s", package_name)

    stack: list[tuple[ModuleType, str]] = [(root_pkg, root_pkg.__name__ + ".")]

    while stack:
        pkg, prefix = stack.pop()
        if not hasattr(pkg, "__path__"):
            continue
        for _finder, name, ispkg in pkgutil.iter_modules(pkg.__path__, prefix):
            mod = _import_module(name)
            if ispkg and mod is not None:
                # Push package module to stack to discover its children.
                stack.append((mod, name + "."))
