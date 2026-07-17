"""Ephemeral secret-file helpers for STAR action runtime delivery.

This module is an internal runtime helper, not a public storage adapter or a
persistent secret store. It creates invocation-owned temporary files under
STAR's runtime sandbox for commands that accept secret file references, and it
provides defensive best-effort cleanup for those files after render or
dispatch failures.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Iterable

from star.core.config import Settings
from star.core.utils.file_storage import ensure_storage_dirs, get_secret_tmp_dir

logger = logging.getLogger("star.actions.runtime.secret_manager")


def create_secret_file(
    secret_value: str,
    *,
    append_newline: bool,
    settings: Settings | None = None,
) -> Path:
    """Create one invocation-owned temporary file containing a secret.

    Args:
        secret_value: Plain secret value for the current invocation.
        append_newline: Whether to append a trailing newline to the file.
        settings: Optional pre-loaded runtime settings.

    Returns:
        Path to the created temporary secret file.

    Raises:
        OSError: If the file cannot be created or written.
    """

    ensure_storage_dirs(settings)
    secret_dir = get_secret_tmp_dir(settings)
    secret_dir.mkdir(parents=True, exist_ok=True)

    fd: int | None = None
    path: Path | None = None
    try:
        fd, raw_path = tempfile.mkstemp(
            prefix="secret_",
            suffix=".tmp",
            dir=secret_dir,
        )
        path = Path(raw_path)
        os.chmod(path, 0o600)
        secret_text = f"{secret_value}\n" if append_newline else secret_value
        with os.fdopen(fd, "wb") as handle:
            fd = None
            handle.write(secret_text.encode("utf-8"))
        return path
    except Exception:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                logger.exception("Failed to close secret file descriptor")
        if path is not None:
            cleanup_secret_files((path,), settings=settings)
        raise


def cleanup_secret_files(
    paths: Iterable[Path],
    *,
    settings: Settings | None = None,
) -> None:
    """Delete invocation-owned temporary secret files best-effort.

    Args:
        paths: Paths created for one rendered action invocation.
        settings: Optional pre-loaded runtime settings.
    """

    owned_paths = tuple(paths)
    if not owned_paths:
        return

    secret_dir = get_secret_tmp_dir(settings)
    try:
        expected_parent = secret_dir.resolve(strict=False)
    except OSError:
        logger.exception("Failed to resolve secret tmp dir during cleanup")
        return

    for path in owned_paths:
        try:
            if path.parent.resolve(strict=False) != expected_parent:
                logger.error("Refusing to cleanup secret file outside secret tmp dir")
                continue
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Failed to delete secret file during cleanup")
