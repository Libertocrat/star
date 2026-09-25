"""CLI regression tests for the built-in Action DSL inspection helper."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_invalid_catalog_exits_nonzero(tmp_path: Path) -> None:
    """
    GIVEN a malformed built-in DSL module in an isolated source tree
    WHEN the inspection helper loads the catalog
    THEN it exits nonzero without reporting compiled actions
    """
    specs_dir = tmp_path / "src/star/actions/specs"
    specs_dir.mkdir(parents=True)
    (specs_dir / "invalid.yml").write_text("module: [broken\n", encoding="utf-8")

    repo_root = Path(__file__).resolve().parents[2]
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("STAR_")
    }
    env["PYTHONPATH"] = str(repo_root / "src")
    result = subprocess.run(  # noqa: S603 -- fixed interpreter and repository script
        [sys.executable, str(repo_root / "scripts/build_action_specs.py")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Failed to load module specs" in result.stdout
    assert "COMPILED ACTIONS" not in result.stdout
