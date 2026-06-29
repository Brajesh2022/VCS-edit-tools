"""Lightweight blob_hash → filepath registry.

Backed by `.vcs_store.json` at the repo root (the nearest ancestor of the
target file that contains a `.git` directory, or the cwd if no repo is
found).

Populated on every `read`, consulted on every `replace`.  Acts as the
fallback when `git ls-files` can't find a file (untracked, modified, etc.).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Optional

# Maximum number of blob entries to keep per filepath in the registry.
# When this limit is exceeded for a given file, the OLDEST entries for
# that file are pruned. This bounds `.vcs_store.json` growth over a long
# agent session. See BUG-4 fix in `register()`.
MAX_BLOBS_PER_FILE = 100


class BlobMismatchError(LookupError):
    """Raised when an edit's claimed blob doesn't match the target file.

    BUG-3 fix: distinguishes three failure modes that all previously
    surfaced as the generic "Merge conflict detected" message:

      1. Blob was never issued by `vcs read` (no snapshot exists, blob is
         not a prefix of the file's current hash, and not in the registry
         under any other file either).
      2. Blob was issued for a DIFFERENT file (snapshot exists but the
         snapshot's content hashes to a different filepath than the agent
         is targeting — i.e. the snapshot is registered under another path).
      3. Genuine concurrent modification (blob was issued for this file,
         snapshot exists for this file, but the file's current content
         hash doesn't match — someone edited it after our read).

    Only case (3) should produce the "Merge conflict detected" message.
    Cases (1) and (2) produce a more helpful "blob was never issued" /
    "blob was issued for another file" error so the agent can debug
    instead of blindly re-reading.
    """

    def __init__(self, kind: str, message: str):
        self.kind = kind  # "never_issued" | "wrong_file" | "conflict"
        super().__init__(message)


def find_repo_root(start: str = ".") -> str:
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


def _repo_storage_dir(repo_root: str) -> Path:
    home = Path.home()
    abs_path = str(Path(repo_root).resolve())
    path_hash = hashlib.md5(abs_path.encode("utf-8")).hexdigest()
    d = home / ".vcs" / path_hash
    d.mkdir(parents=True, exist_ok=True)
    return d


def _store_path(repo_root: str) -> Path:
    return _repo_storage_dir(repo_root) / ".vcs_store.json"


def load_store(repo_root: str) -> dict:
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

    BUG-4 fix (v2.1): the registry previously grew unboundedly — every read
    added a new entry, and short-prefix duplicates were stored as separate
    keys. We now:
      - Skip storing short prefixes (<40 chars for SHA-1) as separate keys.
        The `lookup()` function already does prefix matching, so storing
        short prefixes is redundant AND creates orphan snapshot files.
      - Cap the number of blob entries per filepath at MAX_BLOBS_PER_FILE
        (default 100). When the cap is exceeded for a file, the OLDEST
        entries for that file are pruned (and their snapshot files deleted).

    The "oldest" determination uses an insertion-order list maintained in
    the store under `_order` — we append on insert and trim from the front
    when pruning. This is O(N) per insert but N is bounded by the cap.
    """
    if repo_root is None:
        repo_root = find_repo_root(os.path.dirname(os.path.abspath(filepath)))

    abs_path = os.path.abspath(filepath)
    try:
        rel = os.path.relpath(abs_path, repo_root)
        stored = rel if not rel.startswith("..") else abs_path
    except ValueError:
        stored = abs_path

    # Normalize: always store the FULL hash lowercased. If the caller passes
    # a short prefix (<40 chars), we look up the matching full hash already
    # in the registry and use that. If no match exists, we still store the
    # short prefix so lookups continue to work — but we DON'T separately
    # snapshot it (snapshots dedupe by hash via save_snapshot()).
    # This fixes BUG-4 finding #4: short-prefix duplicates were stored as
    # separate keys, creating orphan snapshot files.
    raw_hash = blob_hash.lower()

    data = load_store(repo_root)
    blobs = data.setdefault("blobs", {})
    order = data.setdefault("_order", [])  # list of (hash, filepath) in insertion order

    # If the caller passed a short prefix, try to find the full hash it
    # refers to. If found, use the full hash. If not found, fall through
    # and store the short prefix (it's all we have).
    full_hash = raw_hash
    if len(raw_hash) < 40:
        for existing_hash in list(blobs.keys()):
            if existing_hash.startswith(raw_hash):
                full_hash = existing_hash
                break

    # If this hash is already registered for the same file, no-op (don't
    # touch insertion order — keeps the existing position).
    if full_hash in blobs and blobs[full_hash] == stored:
        return

    # BUG-4 finding #4 fix: if we're registering the FULL hash and a SHORT
    # prefix of it is already in the registry, remove the short prefix
    # entry — we don't want both. (Only do this when full_hash is the full
    # 40-char SHA-1, not when full_hash is itself a short prefix.)
    if len(full_hash) >= 40:
        short_prefixes_to_remove = [
            h for h in list(blobs.keys())
            if len(h) < 40 and full_hash.startswith(h)
        ]
        for sh in short_prefixes_to_remove:
            blobs.pop(sh, None)
            order = [(hh, p) for (hh, p) in order if hh != sh]

    # Insert / overwrite
    blobs[full_hash] = stored
    # Track insertion order (remove any prior entry for this hash first)
    order = [(h, p) for (h, p) in order if h != full_hash]
    order.append((full_hash, stored))
    data["_order"] = order

    # Prune: if we have more than MAX_BLOBS_PER_FILE entries for THIS
    # filepath, drop the oldest ones (and delete their snapshot files).
    entries_for_file = [(h, p) for (h, p) in order if p == stored]
    if len(entries_for_file) > MAX_BLOBS_PER_FILE:
        # Number to drop
        to_drop_count = len(entries_for_file) - MAX_BLOBS_PER_FILE
        # The oldest `to_drop_count` entries for this file
        to_drop_hashes = set(h for (h, _) in entries_for_file[:to_drop_count])

        for h in to_drop_hashes:
            blobs.pop(h, None)
            # Remove from order list too
            order = [(hh, p) for (hh, p) in order if hh != h]
            # Delete the snapshot file if it exists
            snap = _snapshots_dir(repo_root) / f"{h}.txt"
            try:
                snap.unlink(missing_ok=True)
            except OSError:
                pass
        data["_order"] = order

    _save_store(repo_root, data)


def lookup(blob_hash: str, repo_root: Optional[str] = None) -> Optional[str]:
    """Return the registered filepath for a blob hash, or None.

    The returned path is relative to `repo_root` (or the cwd if repo_root is
    None and we have to discover it).
    """
    blob_hash = blob_hash.lower()

    if repo_root is None:
        repo_root = find_repo_root()

    data = load_store(repo_root)
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
    repo_root = find_repo_root(search_root)
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
        repo_root = find_repo_root()
    _save_store(repo_root, {"blobs": {}, "_order": []})
    snap_dir = _snapshots_dir(repo_root)
    if snap_dir.exists():
        shutil.rmtree(snap_dir, ignore_errors=True)


def gc_store(repo_root: Optional[str] = None, prune_stale: bool = True,
             prune_duplicates: bool = True) -> dict:
    """Garbage-collect the registry and snapshot directory.

    Used by `vcs gc` and `vcs status --prune`. Returns a summary dict with
    counts of what was removed.

    Args:
        repo_root: repo root (auto-discovered if None)
        prune_stale: remove registry entries whose filepath no longer exists
                     on disk (deleted files)
        prune_duplicates: remove orphan snapshot files that have no
                          corresponding registry entry (left over from
                          short-prefix duplicates in v2.0)

    Returns: {"stale_entries": N, "orphan_snapshots": M, "total_remaining": K}
    """
    if repo_root is None:
        repo_root = find_repo_root()
    data = load_store(repo_root)
    blobs = data.get("blobs", {})
    order = data.get("_order", [])

    stale_removed = 0
    if prune_stale:
        # Find entries whose file no longer exists
        to_remove = []
        for h, p in list(blobs.items()):
            abs_p = p if os.path.isabs(p) else os.path.join(repo_root, p)
            if not os.path.exists(abs_p):
                to_remove.append(h)
        for h in to_remove:
            blobs.pop(h, None)
            order = [(hh, p) for (hh, p) in order if hh != h]
            stale_removed += 1
        data["_order"] = order

    orphan_removed = 0
    if prune_duplicates:
        snap_dir = _snapshots_dir(repo_root)
        if snap_dir.exists():
            reg_hashes = set(h.lower() for h in blobs.keys())
            for snap_file in snap_dir.glob("*.txt"):
                if snap_file.stem.lower() not in reg_hashes:
                    try:
                        snap_file.unlink()
                        orphan_removed += 1
                    except OSError:
                        pass

    _save_store(repo_root, data)
    return {
        "stale_entries": stale_removed,
        "orphan_snapshots": orphan_removed,
        "total_remaining": len(blobs),
    }


# ---------------------------------------------------------------------------
# Snapshot store: blob_hash → file content at read time
# ---------------------------------------------------------------------------

def _snapshots_dir(repo_root: str) -> Path:
    d = _repo_storage_dir(repo_root) / ".vcs_snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_snapshot(blob_hash: str, content: str, repo_root: Optional[str] = None) -> None:
    """Persist the file's content at the time it was read, keyed by blob hash.

    This lets us reconstruct `base` for 3-way merge even when the file is
    untracked or git's object store doesn't have the blob.

    BUG-4 fix: if `blob_hash` is a short prefix, we look up the matching
    full hash in the registry and store the snapshot under THAT. This
    prevents orphan snapshot files when the agent passes a short blob
    prefix to `vcs replace` (which calls register() with the prefix and
    then save_snapshot() with the prefix).
    """
    if repo_root is None:
        repo_root = find_repo_root()
    blob_lower = blob_hash.lower()

    # If it's a short prefix, try to resolve to the full hash
    if len(blob_lower) < 40:
        data = load_store(repo_root)
        for existing_hash in data.get("blobs", {}).keys():
            if existing_hash.startswith(blob_lower):
                blob_lower = existing_hash
                break

    snap_path = _snapshots_dir(repo_root) / f"{blob_lower}.txt"
    with snap_path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(content)


def load_snapshot(blob_hash: str, repo_root: Optional[str] = None) -> Optional[str]:
    """Return the snapshot content for a blob hash, or None if not snapshotted.

    Supports short-prefix lookup.
    """
    if repo_root is None:
        repo_root = find_repo_root()
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
