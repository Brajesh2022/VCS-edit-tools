"""Lightweight blob_hash → filepath registry.

Backed by `.vcs_store.json` at the repo root (the nearest ancestor of the
target file that contains a `.git` directory, or the cwd if no repo is
found).

Populated on every `read`, consulted on every `replace`.  Acts as the
fallback when `git ls-files` can't find a file (untracked, modified, etc.).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional


def _find_repo_root(start: str = ".") -> str:
    """Walk up from `start` to find a directory containing `.git`.

    Falls back to `start` (absolute) if no git repo is found.
    """
    start_path = Path(start).resolve()
    if start_path.is_file():
        start_path = start_path.parent

    for candidate in [start_path, *start_path.parents]:
        if (candidate / ".git").exists():
            return str(candidate)
    return str(start_path)


def _store_path(repo_root: str) -> Path:
    return Path(repo_root) / ".vcs_store.json"


def _load_store(repo_root: str) -> dict:
    """Load the registry.  Schema: { "blobs": { "<hash>": "<path>" } }"""
    path = _store_path(repo_root)
    if not path.exists():
        return {"blobs": {}}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or "blobs" not in data:
            return {"blobs": {}}
        if not isinstance(data["blobs"], dict):
            data["blobs"] = {}
        return data
    except (json.JSONDecodeError, OSError):
        return {"blobs": {}}


def _save_store(repo_root: str, data: dict) -> None:
    path = _store_path(repo_root)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)


def register(blob_hash: str, filepath: str, repo_root: Optional[str] = None) -> None:
    """Map a blob hash to a filepath in the registry.

    `filepath` is stored relative to `repo_root` when possible, so the store
    stays portable if the project is moved.
    """
    if repo_root is None:
        repo_root = _find_repo_root(os.path.dirname(os.path.abspath(filepath)))

    abs_path = os.path.abspath(filepath)
    try:
        rel = os.path.relpath(abs_path, repo_root)
        # If file is outside repo, store absolute path
        stored = rel if not rel.startswith("..") else abs_path
    except ValueError:
        stored = abs_path

    data = _load_store(repo_root)
    data["blobs"][blob_hash.lower()] = stored
    _save_store(repo_root, data)


def lookup(blob_hash: str, repo_root: Optional[str] = None) -> Optional[str]:
    """Return the registered filepath for a blob hash, or None.

    The returned path is relative to `repo_root` (or the cwd if repo_root is
    None and we have to discover it).
    """
    blob_hash = blob_hash.lower()

    if repo_root is None:
        repo_root = _find_repo_root()

    data = _load_store(repo_root)
    entry = data["blobs"].get(blob_hash)
    if entry:
        return entry

    # Support short-prefix lookups
    for full_hash, path in data["blobs"].items():
        if full_hash.startswith(blob_hash):
            return path
    return None


def resolve_path(blob_hash: str, search_root: str = ".") -> Optional[str]:
    """High-level lookup: registry first, then filesystem walk via find_file_by_blob.

    Returns a path relative to `search_root` (or absolute if outside the root).

    IMPORTANT: This returns the path the blob hash was *originally* associated
    with, even if the file has since been modified.  Conflict detection (the
    blob mismatch check) happens in `replace.py`.  This function's only job is
    "which file did this blob refer to?".
    """
    # 1. Try the registry — if we have a mapping, return it (don't re-verify
    #    the current hash; the file may legitimately have changed, which is
    #    exactly the conflict case replace.py needs to detect).
    repo_root = _find_repo_root(search_root)
    registered = lookup(blob_hash, repo_root=repo_root)
    if registered:
        candidate = registered if os.path.isabs(registered) else os.path.join(repo_root, registered)
        if os.path.exists(candidate):
            # Return absolute path
            return candidate
    # 2. Fall back to a filesystem / git ls-files walk (this only finds files
    #    whose CURRENT content matches the hash, so it can't help with the
    #    conflict case — but it's still useful for "find me the file with this
    #    content right now" queries).
    from core.blob import find_file_by_blob
    return find_file_by_blob(blob_hash, search_root=search_root)


def clear_store(repo_root: Optional[str] = None) -> None:
    """Wipe the registry AND snapshot store (used by tests)."""
    import shutil
    if repo_root is None:
        repo_root = _find_repo_root()
    _save_store(repo_root, {"blobs": {}})
    snap_dir = Path(repo_root) / ".vcs_snapshots"
    if snap_dir.exists():
        shutil.rmtree(snap_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Snapshot store: blob_hash → file content at read time
# ---------------------------------------------------------------------------

def _snapshots_dir(repo_root: str) -> Path:
    d = Path(repo_root) / ".vcs_snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_snapshot(blob_hash: str, content: str, repo_root: Optional[str] = None) -> None:
    """Persist the file's content at the time it was read, keyed by blob hash.

    This lets us reconstruct `base` for 3-way merge even when the file is
    untracked or git's object store doesn't have the blob.
    """
    if repo_root is None:
        repo_root = _find_repo_root()
    snap_path = _snapshots_dir(repo_root) / f"{blob_hash.lower()}.txt"
    with snap_path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(content)


def load_snapshot(blob_hash: str, repo_root: Optional[str] = None) -> Optional[str]:
    """Return the snapshot content for a blob hash, or None if not snapshotted.

    Supports short-prefix lookup.
    """
    if repo_root is None:
        repo_root = _find_repo_root()
    blob_hash = blob_hash.lower()
    snap_dir = _snapshots_dir(repo_root)

    exact = snap_dir / f"{blob_hash}.txt"
    if exact.exists():
        with exact.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            return fh.read()

    # Prefix lookup
    for p in snap_dir.glob("*.txt"):
        if p.stem.startswith(blob_hash):
            with p.open("r", encoding="utf-8", errors="replace", newline="") as fh:
                return fh.read()
    return None
