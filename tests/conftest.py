"""Shared pytest fixtures.

Provides a temporary git-backed working directory per test so the real
`.vcs_store.json` / `.vcs_snapshots/` are never touched.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Make the project importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def tmp_repo(tmp_path, monkeypatch):
    """A temporary git repo with one commit, cwd'd into it.

    All vcs operations are isolated to this directory.
    """
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], check=True)
    subprocess.run(["git", "config", "user.name", "test"], check=True)
    # Initial commit so .git exists and HEAD is valid
    (tmp_path / ".gitignore").write_text("*.tmp\n")
    subprocess.run(["git", "add", "."], check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], check=True)
    return tmp_path


@pytest.fixture
def sample_file(tmp_repo):
    """A small sample file inside the tmp repo."""
    p = tmp_repo / "sample.py"
    p.write_text("line1\nline2\nline3\nline4\nline5\n")
    return p
