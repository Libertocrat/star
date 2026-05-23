# tests/test_security_path.py
"""
Tests for filesystem path security helpers.

These tests freeze the security invariants that define STAR's sandbox model.
They verify that STAR never escapes its sandbox, never follows symlinks,
and rejects malformed or dangerous paths.
"""

import os

import pytest

from star.core.security.paths import (
    PathSecurityError,
    resolve_in_sandbox,
    safe_open_no_follow,
    sanitize_rel_path,
    validate_path,
)

# ============================================================================
# Path Sanitization
# ============================================================================


def test_sanitize_rejects_empty_path():
    """
    GIVEN an empty user path
    WHEN sanitize_rel_path is called
    THEN a PathSecurityError is raised
    """
    with pytest.raises(PathSecurityError):
        sanitize_rel_path("")


def test_sanitize_rejects_absolute_path():
    """
    GIVEN an absolute user path
    WHEN sanitize_rel_path is called
    THEN a PathSecurityError is raised
    """
    with pytest.raises(PathSecurityError):
        sanitize_rel_path("/etc/passwd")


def test_sanitize_rejects_traversal():
    """
    GIVEN a path containing '..'
    WHEN sanitize_rel_path is called
    THEN a PathSecurityError is raised
    """
    with pytest.raises(PathSecurityError):
        sanitize_rel_path("../secret.txt")


def test_sanitize_rejects_backslashes():
    """
    GIVEN a path containing backslashes
    WHEN sanitize_rel_path is called
    THEN a PathSecurityError is raised
    """
    with pytest.raises(PathSecurityError):
        sanitize_rel_path("..\\secret.txt")


def test_sanitize_rejects_control_characters():
    """
    GIVEN a path containing control characters
    WHEN sanitize_rel_path is called
    THEN a PathSecurityError is raised
    """
    with pytest.raises(PathSecurityError):
        sanitize_rel_path("bad\x00path")


def test_sanitize_normalizes_valid_path():
    """
    GIVEN a valid relative path with redundant segments
    WHEN sanitize_rel_path is called
    THEN the path is normalized and safe
    """
    p = sanitize_rel_path("uploads/./files/test.txt")

    assert p == "uploads/files/test.txt"


# ============================================================================
# Sandbox Resolution
# ============================================================================


def test_resolve_path_inside_sandbox(minimal_safe_env, star_root_dir):
    """
    GIVEN a valid relative path inside the sandbox
    WHEN resolve_in_sandbox is called
    THEN the resolved path is inside the sandbox directory
    """

    (star_root_dir / "uploads").mkdir(parents=True, exist_ok=True)
    (star_root_dir / "uploads" / "file.txt").touch()

    resolved = resolve_in_sandbox(star_root_dir, "uploads/file.txt")

    assert resolved.exists()
    assert resolved.is_file()
    assert str(resolved).startswith(str(star_root_dir))


def test_resolve_rejects_path_outside_sandbox(minimal_safe_env, star_root_dir):
    """
    GIVEN a path that would escape the sandbox
    WHEN resolve_in_sandbox is called
    THEN a PathSecurityError is raised
    """

    with pytest.raises(PathSecurityError):
        resolve_in_sandbox(star_root_dir, "../outside.txt")


def test_resolve_rejects_symlink_component(minimal_safe_env, star_root_dir, tmp_path):
    """
    GIVEN a sandbox directory that contains a symlink as one of its path components
    AND the symlink points outside of the sandbox
    WHEN resolve_in_sandbox is called with a path traversing that symlink
    THEN a PathSecurityError is raised

    This test enforces the invariant that STAR must never follow symlinks
    inside the sandbox, even if the symlink name itself is allowlisted.
    """

    # ------------------------------------------------------------------
    # Arrange: create a real directory OUTSIDE the sandbox
    # ------------------------------------------------------------------
    real_dir = tmp_path / "real_target"
    real_dir.mkdir()

    # ------------------------------------------------------------------
    # Arrange: create a symlink INSIDE the sandbox pointing outside
    # ------------------------------------------------------------------
    symlink = star_root_dir / "malicious_link"
    symlink.symlink_to(real_dir)

    # ------------------------------------------------------------------
    # Act / Assert: resolving a path through the symlink is rejected
    # ------------------------------------------------------------------
    with pytest.raises(PathSecurityError):
        resolve_in_sandbox(star_root_dir, "malicious_link/file.txt")


def test_resolve_allows_any_subdir_under_sandbox(minimal_safe_env, star_root_dir):
    """
    GIVEN a path under the configured sandbox root
    WHEN resolving a path whose first component is arbitrary
    THEN the path is allowed as long as it remains under the sandbox
    """
    (star_root_dir / "other").mkdir()
    (star_root_dir / "other" / "file.txt").touch()

    resolved = resolve_in_sandbox(star_root_dir, "other/file.txt")

    assert resolved.exists()
    assert resolved.is_file()
    assert str(resolved).startswith(str(star_root_dir))


def test_resolve_rejects_missing_sandbox_dir(minimal_safe_env, tmp_path):
    """
    GIVEN a non-existent sandbox directory
    WHEN resolve_in_sandbox is called
    THEN a PathSecurityError is raised
    """
    missing = tmp_path / "does-not-exist"

    with pytest.raises(PathSecurityError):
        resolve_in_sandbox(missing, "file.txt")


# ============================================================================
# safe_open_no_follow - secure file opening
# ============================================================================


def test_safe_open_allows_regular_file(tmp_path):
    """
    GIVEN a regular file inside the sandbox
    WHEN safe_open_no_follow is used
    THEN the file descriptor is returned successfully
    """
    f = tmp_path / "file.txt"
    f.write_text("hello")

    fd = safe_open_no_follow(f, os.O_RDONLY)
    try:
        assert fd >= 0
    finally:
        os.close(fd)


def test_safe_open_rejects_symlink(tmp_path):
    """
    GIVEN a symlink pointing to a file
    WHEN safe_open_no_follow is used
    THEN a PathSecurityError is raised
    """
    target = tmp_path / "real.txt"
    target.write_text("secret")

    link = tmp_path / "link.txt"
    link.symlink_to(target)

    with pytest.raises(PathSecurityError):
        safe_open_no_follow(link, os.O_RDONLY)


def test_safe_open_rejects_non_regular_file(tmp_path):
    """
    GIVEN a directory path
    WHEN safe_open_no_follow is used
    THEN a PathSecurityError is raised
    """
    d = tmp_path / "dir"
    d.mkdir()

    with pytest.raises(PathSecurityError):
        safe_open_no_follow(d, os.O_RDONLY)


# ============================================================================
# validate_path - centralized handler helper
# ============================================================================


def test_validate_path_open_returns_fd(minimal_safe_env, star_root_dir):
    """
    GIVEN a regular file inside the sandbox
    WHEN validate_path is called with open_no_follow=True
    THEN it returns the resolved path and an owned fd
    """
    file_path = star_root_dir / "tmp" / "file.txt"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("hello")

    validated = validate_path(
        user_path="tmp/file.txt",
        open_no_follow=True,
        require_exists=True,
    )

    try:
        assert validated.path == file_path
        assert validated.fd is not None
        assert validated.fd >= 0
    finally:
        if validated.fd is not None:
            os.close(validated.fd)


def test_validate_path_missing_allowed_when_not_required(minimal_safe_env):
    """
    GIVEN a missing file inside sandbox
    WHEN validate_path is called with require_exists=False and open_no_follow=True
    THEN no error is raised and fd is None
    """
    validated = validate_path(
        user_path="tmp/missing.bin",
        open_no_follow=True,
        require_exists=False,
    )

    assert validated.path.name == "missing.bin"
    assert "tmp" in validated.path.parts
    assert validated.fd is None


def test_validate_path_rejects_traversal(minimal_safe_env):
    """
    GIVEN a path traversal attempt
    WHEN validate_path is called
    THEN a PathSecurityError is raised
    """
    with pytest.raises(PathSecurityError):
        validate_path(user_path="../outside.txt", open_no_follow=False)
