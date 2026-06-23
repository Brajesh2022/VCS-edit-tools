"""Read a file and return content + blob hash for subsequent edits.

The blob hash always represents the WHOLE file, not the visible window.
The 800-line read limit is purely a display cap to control token usage.
"""

from __future__ import annotations

from pathlib import Path

from core.blob import get_blob_hash
from core.store import register, save_snapshot

MAX_READ_LINES = 800


def _parse_range(start: int, end: int | None, total: int) -> tuple[int, int]:
    """Validate / clamp a (start, end) line range against file bounds.

    Rules:
      - start defaults to 1
      - end defaults to total
      - start must be >= 1
      - end clamps to total
      - start must be <= end (after clamping)
    """
    if start < 1:
        start = 1
    if end is None:
        end = total
    if end > total:
        end = total
    if start > end:
        # empty range; return a no-op window
        start = end if end >= 1 else 1
    return start, end


def read_file(filepath: str, start: int = 1, end: int | None = None) -> dict:
    """Read a file (optionally a sub-range) and return a structured result.

    Args:
        filepath: path to the file (relative or absolute)
        start: 1-indexed first line to show (default 1)
        end: 1-indexed last line to show (default: EOF)

    Returns:
        {
          "blob": "a3f9...",            # hash of FULL file, not just window
          "path": "src/auth.js",
          "total_lines": 1200,
          "shown_range": "1-800",
          "content": "line1\nline2\n...",
          "truncated": 400,             # 0 if no truncation
          "next_command": "vcs read src/auth.js 801-1200"  # null if no truncation
        }

    Raises:
        FileNotFoundError: if the file doesn't exist
        IsADirectoryError: if filepath is a directory
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"file not found: {filepath}")
    if path.is_dir():
        raise IsADirectoryError(f"path is a directory: {filepath}")

    blob = get_blob_hash(str(path))
    register(blob, str(path))

    # Read all lines preserving line endings
    with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        content_raw = fh.read()

    # Snapshot the FULL file content keyed by blob hash, so we can reconstruct
    # `base` for 3-way merge even when the file is untracked / not in git's
    # object store.
    save_snapshot(blob, content_raw)

    # Splitlines keeps no trailing newline on the last element; we need to
    # preserve them so we use splitlines(keepends=True).
    lines = content_raw.splitlines(keepends=True)
    total = len(lines)

    start, end = _parse_range(start, end, total)

    # Enforce MAX_READ_LINES window
    requested = end - start + 1
    if requested > MAX_READ_LINES:
        truncated_end = start + MAX_READ_LINES - 1
        truncated_count = end - truncated_end
        next_cmd = f"vcs read {filepath} {truncated_end + 1}-{end}"
        end = truncated_end
    else:
        truncated_count = 0
        next_cmd = None

    # Slice (1-indexed inclusive → 0-indexed exclusive end)
    # Prefix each line with its line number, e.g., '1: content\n'
    window_lines = lines[start - 1 : end]
    numbered_lines = [f"{start + i}: {line}" for i, line in enumerate(window_lines)]
    window = "".join(numbered_lines)

    return {
        "blob": blob,
        "path": str(filepath),
        "total_lines": total,
        "shown_range": f"{start}-{end}",
        "content": window,
        "truncated": truncated_count,
        "next_command": next_cmd,
    }
