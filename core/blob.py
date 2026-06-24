"""Git blob hashing and reverse-lookup helpers.

A git blob hash is the SHA-1 of the literal blob content, prefixed with
"blob <size>\\0".  It is stable per file content (regardless of file path)
and is the foundation of this tool's conflict-detection mechanism.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path


def _git_hash_bytes(data: bytes) -> str:
    """Compute a git blob hash for raw bytes without spawning a subprocess.

    Equivalent to `git hash-object` but pure-python so we don't depend on a
    working git repo to hash arbitrary content (e.g. untracked files).
    """
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def get_blob_hash(filepath: str) -> str:
    """Return the git blob hash of a file's current on-disk content.

    Uses the pure-python implementation so it works on untracked/modified
    files without needing `git add` to have run.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"file not found: {filepath}")
    with path.open("rb") as fh:
        return _git_hash_bytes(fh.read())


def find_file_by_blob(blob_hash: str, search_root: str = ".") -> str | None:
    """Walk a repo to find which file currently has this blob hash.

    Strategy:
      1. Try `git ls-files` first (fast, only works for tracked, un-modified
         files in a real git repo).
      2. Fall back to a filesystem walk comparing hashes.

    Returns the relative path (from search_root) of the first match, or None.
    The hash is matched case-insensitively but normally compared in full
    (40 chars for SHA-1).  Short prefixes are accepted for ergonomics.
    """
    blob_hash = blob_hash.lower()

    # --- Strategy 1: git ls-files ---
    try:
        result = subprocess.run(
            ["git", "ls-files", "-s"],
            cwd=search_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                # Format: <mode> <hash> <stage>\t<path>
                parts = line.split()
                if len(parts) >= 4:
                    file_hash = parts[1].lower()
                    if file_hash == blob_hash or file_hash.startswith(blob_hash):
                        # Make sure on-disk content still matches (handles modified-but-tracked files)
                        rel_path = parts[3]
                        try:
                            current = get_blob_hash(os.path.join(search_root, rel_path))
                            if current.lower() == file_hash:
                                return rel_path
                        except FileNotFoundError:
                            continue
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # git not available, fall through to filesystem walk

    # --- Strategy 2: filesystem walk ---
    search_root_path = Path(search_root)
    scanned = 0
    max_scan = 10000
    for root, dirs, files in os.walk(search_root_path):
        # Skip common large/ignored directories to speed up normal repos
        dirs[:] = [d for d in dirs if d not in (".git", ".vcs_snapshots", "node_modules", "venv", ".venv", "__pycache__")]
        for fname in files:
            scanned += 1
            if scanned > max_scan:
                return None # Fail fast instead of hanging on huge directories
            fpath = os.path.join(root, fname)
            try:
                if get_blob_hash(fpath).lower().startswith(blob_hash):
                    return os.path.relpath(fpath, search_root_path)
            except OSError:
                continue
    return None
