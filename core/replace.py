"""Main replace logic: blob → range → new content → file (or conflict).

Steps:
  1. Lookup filepath from blob hash (store → fallback to git/filesystem walk)
  2. Get current blob hash of that file
  3. Compare: current_blob == provided_blob?
       YES → clean replace, go to step 4
       NO  → conflict detected, delegate to conflict.py
  4. Splice: lines[:start-1] + new_content_lines + lines[end:]
  5. Write back to file
  6. Compute new blob hash
  7. Register new blob → filepath
  8. Return new blob hash

Errors are raised as exceptions; the CLI layer converts them to JSON.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from core.blob import get_blob_hash
from core.store import resolve_path, register, load_snapshot, save_snapshot
from core.conflict import resolve as conflict_resolve


# ---------------------------------------------------------------------------
# Range parsing
# ---------------------------------------------------------------------------

def parse_line_range(line_range: str, total_lines: int) -> tuple[int, int]:
    """Parse 'START-END' (1-indexed inclusive) and validate against total.

    Accepts:
      '8-50'   → (8, 50)
      '42'     → (42, 42)  single line
      '8-'     → (8, total_lines)
      '-50'    → (1, 50)

    Clamps to [1, total_lines].
    """
    if "-" not in line_range:
        n = int(line_range)
        if n < 1:
            n = 1
        if n > total_lines:
            n = total_lines
        return n, n

    a_str, b_str = line_range.split("-", 1)
    a = int(a_str) if a_str.strip() else 1
    b = int(b_str) if b_str.strip() else total_lines

    if a < 1:
        a = 1
    if b > total_lines:
        b = total_lines
    return a, b


# ---------------------------------------------------------------------------
# Snapshot helpers (for conflict resolution)
# ---------------------------------------------------------------------------

def _snapshot_to_tempfile(content: str, prefix: str = "vcs_base_") -> str:
    """Write `content` to a temp file and return its path."""
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def replace(blob_hash: str, line_range: str, content_file: str, search_root: str = ".") -> dict:
    """Replace lines [line_range] in the file identified by `blob_hash`.

    Args:
        blob_hash:    the blob hash the agent received from `read` (short or full)
        line_range:   'START-END' 1-indexed inclusive (e.g. '8-50')
        content_file: path to a file containing the new content to splice in

    Returns:
        Success:
            {"status": "ok", "new_blob": "b124...", "path": "src/auth.js",
             "new_total_lines": 1234}
        Conflict (passed through from conflict.py):
            {"status": "conflict", "conflicting_lines": "12-20",
             "base_content": "...", "their_change": "...",
             "your_change": "...", "diff": "..."}

    Raises:
        LookupError:   blob hash not found in registry or filesystem
        FileNotFoundError: content_file missing
        ValueError:    malformed line_range
    """
    # 1. Resolve filepath from blob hash
    filepath = resolve_path(blob_hash, search_root=search_root)
    if filepath is None:
        raise LookupError(f"blob hash '{blob_hash}' not found in registry or repo")

    filepath = str(filepath)
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"resolved path no longer exists: {filepath}")

    # 2. Compute current blob hash of the file on disk
    current_blob = get_blob_hash(filepath)

    # 3. Read current file (we'll need this either way)
    with open(filepath, "r", encoding="utf-8", errors="replace", newline="") as fh:
        current_content = fh.read()
    current_lines = current_content.splitlines(keepends=True)
    total = len(current_lines)

    # 4. Read the new content from the tmp file
    if not os.path.exists(content_file):
        raise FileNotFoundError(f"content file not found: {content_file}")
    with open(content_file, "r", encoding="utf-8", errors="replace", newline="") as fh:
        new_content = fh.read()
    new_lines = new_content.splitlines(keepends=True)

    # 5. Parse the range
    start, end = parse_line_range(line_range, total)

    # 6. Compare blobs
    if current_blob.lower() == blob_hash.lower() or current_blob.lower().startswith(blob_hash.lower()):
        # --- CLEAN REPLACE ---
        spliced = current_lines[: start - 1] + new_lines + current_lines[end:]
        new_full_content = "".join(spliced)

        # Atomic write: write to temp, then rename
        fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(os.path.abspath(filepath)) or ".",
            prefix=".vcs_write_",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(new_full_content)
            os.replace(tmp_path, filepath)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

        new_blob = get_blob_hash(filepath)
        register(new_blob, filepath)
        save_snapshot(new_blob, new_full_content)

        return {
            "status": "ok",
            "new_blob": new_blob,
            "path": filepath,
            "new_total_lines": len(spliced),
        }

    # --- CONFLICT PATH ---
    # We need to reconstruct `base` (the file at the agent's blob hash) to
    # feed into the 3-way merge.
    #
    # Strategy (try in order):
    #   1. Local snapshot store (.vcs_snapshots/<hash>.txt) — always available
    #      if `read` was called first (which is the expected workflow).
    #   2. `git cat-file -p <blob_hash>` — works for committed/tracked blobs.
    #   3. Soft conflict: can't reconstruct base, ask agent to re-read.

    base_content: str | None = load_snapshot(blob_hash)

    if base_content is None:
        import subprocess
        try:
            result = subprocess.run(
                ["git", "cat-file", "-p", blob_hash],
                capture_output=True,
                timeout=10,
                check=False,
            )
            if result.returncode == 0:
                base_content = result.stdout.decode("utf-8", errors="replace")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    if base_content is None:
        # Cannot reconstruct base.  Report a soft conflict: tell the agent
        # the file has changed and provide the current content for them to
        # re-read.
        return {
            "status": "conflict",
            "conflicting_lines": line_range,
            "base_content": None,
            "their_change": current_content,
            "your_change": new_content,
            "diff": "",
            "message": (
                "File has changed since the blob hash was issued, and the "
                "original blob is no longer available. Please re-read the "
                "file to get a fresh blob hash, then retry."
            ),
        }

    # Write base to a temp file and run the 3-way merge
    base_tmp = _snapshot_to_tempfile(base_content, prefix="vcs_base_")
    ours_tmp = _snapshot_to_tempfile(new_content, prefix="vcs_ours_")
    theirs_tmp = _snapshot_to_tempfile(current_content, prefix="vcs_theirs_")

    try:
        result = conflict_resolve(
            base_file=base_tmp,
            ours_file=ours_tmp,
            theirs_file=theirs_tmp,
            line_range=line_range,
        )
    finally:
        for p in (base_tmp, ours_tmp, theirs_tmp):
            try:
                os.remove(p)
            except OSError:
                pass

    # If auto-merged, write the merged content back to the file
    if result.get("status") == "auto_merged":
        merged_content = result["new_content"]
        fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(os.path.abspath(filepath)) or ".",
            prefix=".vcs_write_",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(merged_content)
            os.replace(tmp_path, filepath)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

        new_blob = get_blob_hash(filepath)
        register(new_blob, filepath)
        save_snapshot(new_blob, merged_content)

        return {
            "status": "auto_merged",
            "new_blob": new_blob,
            "path": filepath,
            "new_total_lines": len(merged_content.splitlines(keepends=True)),
            "merged_regions": result.get("merged_regions", []),
        }

    # Pass the conflict through to the caller
    return result
