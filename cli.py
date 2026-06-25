#!/usr/bin/env python3
"""VCS Edit Tool — Rich CLI for AI agents.

A blob-hash-based file editing CLI with heredoc support, rich output,
and 3-way merge conflict resolution.

Commands:
    vcs read <filepath> [start-end]
    vcs replace <filepath> <blob> <start-end> << 'EOF' ... EOF
    vcs insert <filepath> <blob> <line> << 'EOF' ... EOF
    vcs delete <filepath> <blob> <start-end>          (delete line range)
    vcs delete <filepath>                              (delete file or directory)
    vcs create <filepath> << 'EOF' ... EOF             (create new file with content)
    vcs batch << 'EOF' [...json...] EOF
    vcs diff <filepath> <blob>
    vcs skeleton <filepath> [start-end]
    vcs tree [path] [--depth N] [--all]
    vcs grep <query> [path] [-i] [-r]
    vcs fmt [--check] [path]
    vcs test <command> [path]
    vcs status

Exit codes:
    0 → success (status=ok or status=auto_merged)
    1 → conflict (agent should handle)
    2 → error (bad args, file not found, etc.)
"""
from __future__ import annotations

import difflib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

# Allow running as a script (python cli.py) and as an installed entry point.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.read import read_file, MAX_READ_LINES, BinaryFileError
from core.replace import replace as do_replace
from core.blob import get_blob_hash
from core.store import (
    register, resolve_path, save_snapshot, load_snapshot,
    _find_repo_root, _load_store, gc_store, BlobMismatchError,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _emit_json(payload: dict, exit_code: int) -> None:
    """Print payload as JSON and exit."""
    if "blob" in payload and isinstance(payload["blob"], str):
        payload["blob"] = payload["blob"][:8]
    if "new_blob" in payload and isinstance(payload["new_blob"], str):
        payload["new_blob"] = payload["new_blob"][:8]
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.exit(exit_code)


def _error(message: str, code: int = 2) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(code)


def _looks_like_blob(s: str) -> bool:
    """Heuristic: hex string of >=6 chars looks like a blob hash."""
    if not s or len(s) < 6:
        return False
    try:
        int(s, 16)
        return True
    except ValueError:
        return False


def _resolve_target(filepath: str, blob: Optional[str] = None) -> str:
    """Resolve and validate the (filepath, blob) pair.

    New contract (per v2 spec): BOTH filepath AND blob are required for edits.
    The blob proves the agent read the file; the filepath confirms which file.

    Args:
        filepath: path to the file (must exist for edits)
        blob:     short or full blob hash the agent received from `vcs read`

    Returns:
        The CLAIMED blob hash (the agent's blob, normalized to a full hash if
        possible). This is what gets passed to do_replace() so the conflict
        detection can compare claimed-blob vs current-blob.

    Raises:
        FileNotFoundError: filepath doesn't exist
        BlobMismatchError: blob was never issued, OR was issued for a
                           different file. (Genuine concurrent modifications
                           are NOT raised here — they're detected later in
                           do_replace() and surface as a conflict result.)
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"file not found: {filepath}")

    current_blob = get_blob_hash(filepath)

    # If blob is None (e.g. skeleton command), use the current blob.
    claimed_blob = blob if blob else current_blob

    # BUG-3 fix: distinguish three blob-mismatch cases BEFORE registering.
    # Only the "genuine concurrent modification" case should surface as a
    # conflict later — the other two are programming errors by the agent
    # and deserve specific, actionable error messages.
    blob_lower = claimed_blob.lower()
    current_lower = current_blob.lower()
    matches_current = (
        current_lower == blob_lower or current_lower.startswith(blob_lower)
    )

    if not matches_current:
        # Blob doesn't match the file's current content. Three possibilities:
        #   (a) Blob was never issued by vcs read at all (no snapshot, not
        #       registered under any file).
        #   (b) Blob was issued for a DIFFERENT file (registered under
        #       another path, or snapshot exists but its content hashes to
        #       a different file).
        #   (c) Blob was issued for THIS file but the file has since been
        #       modified externally — genuine concurrent modification.
        snapshot = load_snapshot(blob_lower)
        # Check registry: is this blob registered under any path?
        repo_root = _find_repo_root(os.path.dirname(os.path.abspath(filepath)))
        from core.store import lookup as _lookup
        registered_path = _lookup(blob_lower, repo_root=repo_root)

        # Normalize the target path for comparison
        abs_target = os.path.abspath(filepath)
        try:
            rel_target = os.path.relpath(abs_target, repo_root)
            if rel_target.startswith(".."):
                rel_target = abs_target
        except ValueError:
            rel_target = abs_target

        if snapshot is None and registered_path is None:
            # Case (a): blob was never issued. The agent probably
            # hallucinated it or copied the wrong value.
            raise BlobMismatchError(
                "never_issued",
                f"blob '{_short_blob(blob_lower)}' was never issued by "
                f"`vcs read`. Read the file first to get a valid blob: "
                f"`vcs read {filepath}`",
            )
        elif registered_path and registered_path != rel_target and registered_path != abs_target:
            # Case (b): blob is registered under a DIFFERENT path.
            raise BlobMismatchError(
                "wrong_file",
                f"blob '{_short_blob(blob_lower)}' was issued for "
                f"'{registered_path}', not for '{rel_target}'. "
                f"Re-read the target file to get a fresh blob.",
            )
        # else: snapshot exists and/or registered under this same path →
        # case (c), genuine concurrent modification. Fall through and let
        # do_replace() detect the conflict and return a conflict result.

    # Register the claimed blob → filepath so resolve_path can find the file.
    register(claimed_blob.lower(), filepath)

    # If the agent's blob matches the current file content, save the snapshot
    # (this is the "clean read" case — no conflict will occur).
    # If they don't match, the snapshot for the claimed blob should already
    # exist from the prior `vcs read` call. We deliberately DO NOT overwrite it.
    if matches_current:
        with open(filepath, "r", encoding="utf-8", errors="replace", newline="") as fh:
            save_snapshot(current_blob, fh.read())
        return current_blob

    # Mismatch case (c): file has been modified since the agent read it.
    # The snapshot for the claimed blob is what `vcs read` saved earlier.
    # We pass the claimed blob to do_replace() so it can detect the conflict.
    return claimed_blob


def _parse_range_arg(range_str: str | None) -> tuple[int | None, int | None]:
    """Parse '8-50' or '519-639' into (start, end). None means full file."""
    if range_str is None:
        return None, None
    if "-" not in range_str:
        n = int(range_str)
        return n, n
    a_str, b_str = range_str.split("-", 1)
    a = int(a_str) if a_str.strip() else 1
    b = int(b_str) if b_str.strip() else None
    return a, b


def _read_stdin() -> str:
    """Read all of stdin (for heredoc support)."""
    if sys.stdin.isatty():
        _error("expected content on stdin (use heredoc: << 'EOF' ... EOF)")
    return sys.stdin.read()


def _write_temp(content: str) -> str:
    """Write content to a temp file, return path."""
    fd, path = tempfile.mkstemp(prefix=".vcs_content_", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
        fh.write(content)
    return path


def _short_blob(h: str) -> str:
    return h[:8] if h else ""


# ---------------------------------------------------------------------------
# Command: read
# ---------------------------------------------------------------------------
def _find_symbol_range(filepath: str, symbol_name: str) -> tuple[int, int]:
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    if filepath.endswith('.py'):
        import ast
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if hasattr(node, 'name') and node.name == symbol_name:
                    return getattr(node, 'lineno', 1), getattr(node, 'end_lineno', len(content.splitlines()))
        except Exception:
            pass

    import re
    lines = content.splitlines()
    pattern = re.compile(rf"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|class|const|let|var)\s+{symbol_name}\b")
    start_line = None
    for i, line in enumerate(lines):
        if pattern.search(line) or (f"{symbol_name}(" in line and "{" in line) or (f"{symbol_name} = " in line and "=>" in line):
            start_line = i
            break

    if start_line is None:
        raise ValueError(f"symbol '{symbol_name}' not found")

    open_braces = 0
    found_brace = False
    for i in range(start_line, len(lines)):
        line = lines[i]
        for char in line:
            if char == '{':
                open_braces += 1
                found_brace = True
            elif char == '}':
                open_braces -= 1
        if found_brace and open_braces <= 0:
            return start_line + 1, i + 1

    return start_line + 1, start_line + 20


def cmd_read(args: list[str]) -> None:
    """vcs read <filepath> [start-end] [--symbol <name>]"""
    if not args:
        _error("usage: vcs read <filepath> [start-end] [--symbol <name>]")

    symbol = None
    if "--symbol" in args:
        idx = args.index("--symbol")
        symbol = args[idx + 1]
        args.pop(idx)
        args.pop(idx)

    filepath = args[0]
    range_str = args[1] if len(args) > 1 else None

    if symbol:
        try:
            start, end = _find_symbol_range(filepath, symbol)
        except Exception as e:
            _error(str(e))
    else:
        try:
            start, end = _parse_range_arg(range_str)
        except ValueError:
            _error(f"invalid line range: '{range_str}'. Expected START-END (e.g. 801-1200).")

    try:
        result = read_file(filepath, start=start or 1, end=end)
    except FileNotFoundError as e:
        _error(str(e))
    except IsADirectoryError as e:
        _error(str(e))
    except BinaryFileError as e:
        # BUG-2 fix: surface a clean error for binary files instead of
        # dumping garbled bytes. Exit code 2 (error), not 1 (conflict).
        _error(str(e))
    except Exception as e:
        _error(f"{type(e).__name__}: {e}")

    # Auto-fallback to skeleton for >800 lines with no range specified
    if end is None and result.get("total_lines", 0) > 800:
        try:
            # OPT-1 fix: call skeletonizer in-process instead of spawning
            # a subprocess. Saves ~80ms per >800-line read.
            import skeleton as _skeleton_mod
            skel_data = _skeleton_mod.generate_skeleton(Path(filepath))
            result["content"] = (
                f"--- NOTE: File is {result['total_lines']} lines (> 800). "
                f"Auto-falling back to skeleton view. ---\n"
                f"--- To read full lines, use: vcs read {filepath} START-END ---\n\n"
            ) + skel_data["output"]
            result["shown_range"] = "skeleton"
            result["truncated"] = 0
            result["next_command"] = None
        except Exception:
            pass  # fallback failed, return truncated raw lines

    blob = _short_blob(result.get("blob", ""))

    # Rich output
    print(f"blob: {blob}")
    print(f"path: {result.get('path', filepath)}")
    shown_range = result.get("shown_range", "")
    if shown_range == "skeleton":
        print(f"Code Lines: 1 to {result['total_lines']} (skeleton)")
    else:
        if "-" in shown_range:
            start_l, end_l = shown_range.split("-", 1)
            print(f"Code Lines: {start_l} to {end_l}")
        else:
            print(f"Code Lines: {shown_range}")

    if result.get("truncated"):
        print(f"truncated: {result['truncated']} lines remaining")
    if result.get("next_command"):
        print(f"next: {result['next_command']}")
    print("---")
    # Content already has line numbers from core/read.py
    print(result["content"], end="")


# ---------------------------------------------------------------------------
# Command: replace  (requires BOTH filepath AND blob)
# ---------------------------------------------------------------------------

def cmd_replace(args: list[str]) -> None:
    """vcs replace <filepath> <blob> <start-end> << 'EOF' ... EOF"""
    if len(args) < 3:
        _error("usage: vcs replace <filepath> <blob> <start-end> << 'EOF'\\nnew content\\nEOF")

    filepath = args[0]
    blob = args[1]
    line_range = args[2]
    content = _read_stdin()

    if content and not content.endswith('\n'):
        content += '\n'

    try:
        blob_hash = _resolve_target(filepath, blob)
    except FileNotFoundError as e:
        _error(str(e))
    except BlobMismatchError as e:
        # BUG-3 fix: never-issued / wrong-file mismatches are clean errors,
        # NOT conflicts. Exit 2 with the specific message.
        _error(str(e))

    tmp_path = _write_temp(content)

    try:
        search_root = os.path.dirname(os.path.abspath(filepath))
        result = do_replace(blob_hash, line_range, tmp_path, search_root=search_root)
    except LookupError as e:
        _error(str(e))
    except FileNotFoundError as e:
        _error(str(e))
    except ValueError as e:
        _error(str(e))
    except Exception as e:
        _error(f"{type(e).__name__}: {e}")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    _print_edit_result(result)


# ---------------------------------------------------------------------------
# Command: insert  (requires BOTH filepath AND blob)
# ---------------------------------------------------------------------------

def cmd_insert(args: list[str]) -> None:
    """vcs insert <filepath> <blob> <line> << 'EOF' ... EOF"""
    if len(args) < 3:
        _error("usage: vcs insert <filepath> <blob> <line> << 'EOF'\\ncontent\\nEOF")

    filepath = args[0]
    blob = args[1]
    line_str = args[2]

    try:
        line_no = int(line_str)
    except ValueError:
        _error(f"invalid line number: '{line_str}'")
        return

    if line_no < 1:
        _error(f"line number must be >= 1, got {line_no}")

    content = _read_stdin()
    if content and not content.endswith('\n'):
        content += '\n'

    try:
        blob_hash = _resolve_target(filepath, blob)
    except FileNotFoundError as e:
        _error(str(e))
    except BlobMismatchError as e:
        _error(str(e))

    tmp_path = _write_temp(content)

    try:
        search_root = os.path.dirname(os.path.abspath(filepath))
        # Insert = replace with zero-width range (line_no to line_no-1)
        result = do_replace(blob_hash, f"{line_no}-{line_no - 1}", tmp_path, search_root=search_root)
    except LookupError as e:
        _error(str(e))
    except FileNotFoundError as e:
        _error(str(e))
    except ValueError as e:
        _error(str(e))
    except Exception as e:
        _error(f"{type(e).__name__}: {e}")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    _print_edit_result(result)


# ---------------------------------------------------------------------------
# Command: delete  (dispatch: line-range vs file/dir)
# ---------------------------------------------------------------------------

def cmd_delete(args: list[str]) -> None:
    """vcs delete <filepath> <blob> <start-end>   (delete line range)
       vcs delete <filepath>                       (delete file or directory)
    """
    if not args:
        _error("usage: vcs delete <filepath> [<blob> <start-end>]")

    filepath = args[0]

    # ── Dispatch: 3 args = line-range delete; 1 arg = file/dir delete ──
    if len(args) == 1:
        _delete_path(filepath)
        return

    if len(args) < 3:
        _error("usage: vcs delete <filepath> <blob> <start-end>   (line-range mode)\n"
               "       vcs delete <filepath>                       (file/dir mode)")

    blob = args[1]
    line_range = args[2]

    try:
        blob_hash = _resolve_target(filepath, blob)
    except FileNotFoundError as e:
        _error(str(e))
    except BlobMismatchError as e:
        _error(str(e))

    try:
        search_root = os.path.dirname(os.path.abspath(filepath))
        result = do_replace(blob_hash, line_range, os.devnull, search_root=search_root)
    except LookupError as e:
        _error(str(e))
    except FileNotFoundError as e:
        _error(str(e))
    except ValueError as e:
        _error(str(e))
    except Exception as e:
        _error(f"{type(e).__name__}: {e}")

    _print_edit_result(result)


def _delete_path(filepath: str) -> None:
    """Delete a file or an entire directory tree."""
    if not os.path.exists(filepath):
        _error(f"path not found: {filepath}")

    try:
        if os.path.isdir(filepath) and not os.path.islink(filepath):
            shutil.rmtree(filepath)
        else:
            os.remove(filepath)
    except OSError as e:
        _error(f"failed to delete '{filepath}': {e}")

    # Clean output: just `status: ok`
    print("status: ok")
    sys.exit(0)


# ---------------------------------------------------------------------------
# Command: create  (new — create a file with content)
# ---------------------------------------------------------------------------

def cmd_create(args: list[str]) -> None:
    """vcs create <filepath> << 'EOF' ... EOF"""
    if not args:
        _error("usage: vcs create <filepath> << 'EOF'\\ncontent\\nEOF")

    filepath = args[0]
    content = _read_stdin()

    # Don't silently overwrite an existing file
    if os.path.exists(filepath):
        _error(f"file already exists: {filepath} (use `vcs replace` to edit)")

    # Create parent directories if needed
    parent = os.path.dirname(os.path.abspath(filepath))
    tmp_path = None  # initialized upfront so the except block can safely reference it

    if content and not content.endswith('\n'):
        content += '\n'

    try:
        os.makedirs(parent, exist_ok=True)
        # Atomic write: temp file in same dir, then rename
        fd, tmp_path = tempfile.mkstemp(
            dir=parent or ".",
            prefix=".vcs_create_",
        )
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        os.replace(tmp_path, filepath)
    except OSError as e:
        # Cleanup temp file on failure (tmp_path may still be None if mkstemp failed)
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        _error(f"failed to create '{filepath}': {e}")

    # Register the new file's blob so the agent can immediately edit it
    try:
        blob_hash = get_blob_hash(filepath)
        register(blob_hash, filepath)
        save_snapshot(blob_hash, content)
    except Exception:
        pass

    # Clean output: just `status: ok`
    print("status: ok")
    sys.exit(0)


# ---------------------------------------------------------------------------
# Command: batch  (BOTH filepath AND blob required per edit)
# ---------------------------------------------------------------------------

def cmd_batch(args: list[str]) -> None:
    """vcs batch << 'EOF' [...json...] EOF

    Each edit MUST include BOTH `filepath` AND `blob`. Missing either → rejected.
    """
    raw = _read_stdin()

    def _parse_batch_input(raw: str) -> list[dict]:
        raw_stripped = raw.strip()
        if raw_stripped.startswith('['):
            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                _error(f"invalid JSON: {e}")

        edits = []
        current_edit = None
        content_lines = []

        for line in raw.splitlines(keepends=True):
            if line.startswith("=== ") and " ===" in line:
                if current_edit:
                    current_edit["content"] = "".join(content_lines)
                    edits.append(current_edit)

                parts = line.strip().split(" ")
                # Format: === <OP> <filepath> <blob> <range> ===   (v2, 6 parts)
                # OR legacy v1: === <OP> <target> <range> ===       (5 parts, blob missing)
                # We accept both so the v1 format gets cleanly rejected by the
                # validation loop below with a helpful "missing blob" message
                # instead of being silently dropped.
                if len(parts) >= 6:
                    op = parts[1].lower()
                    filepath = parts[2]
                    blob = parts[3]
                    rng = parts[4]
                    current_edit = {
                        "type": op,
                        "filepath": filepath,
                        "blob": blob,
                    }
                    if op in ("replace", "delete"):
                        current_edit["line_range"] = rng
                    elif op == "insert":
                        current_edit["line_str"] = rng
                    content_lines = []
                elif len(parts) == 5:
                    # Legacy v1 format: === <OP> <target> <range> ===
                    # Parse it but leave blob=None so the validation loop
                    # rejects it cleanly with "missing blob".
                    op = parts[1].lower()
                    filepath = parts[2]
                    rng = parts[3]
                    current_edit = {
                        "type": op,
                        "filepath": filepath,
                        "blob": None,  # will be rejected below
                    }
                    if op in ("replace", "delete"):
                        current_edit["line_range"] = rng
                    elif op == "insert":
                        current_edit["line_str"] = rng
                    content_lines = []
                else:
                    content_lines.append(line)
            else:
                if current_edit:
                    content_lines.append(line)

        if current_edit:
            current_edit["content"] = "".join(content_lines)
            edits.append(current_edit)

        return edits

    edits = _parse_batch_input(raw)

    if not isinstance(edits, list):
        _error("expected a JSON array of edit objects")

    # Empty input or input that parsed to zero edits is an error (likely bad input)
    if not edits:
        _error("no edits parsed from input — expected JSON array or `=== OP filepath blob range ===` blocks")

    # Validate: every edit must be a dict with BOTH filepath AND blob.
    # Non-dict JSON elements (e.g. `[1, 2, 3]`) are rejected explicitly to
    # avoid a confusing AttributeError when we try to call .get() on them.
    for i, edit in enumerate(edits):
        if not isinstance(edit, dict):
            print(f"  [{i}] REJECTED: edit must be a JSON object (got {type(edit).__name__})")
            sys.exit(2)
        # Backward-compat: accept `target` and copy to filepath if no filepath
        if "filepath" not in edit and "target" in edit:
            edit["filepath"] = edit["target"]
        if not edit.get("blob"):
            print(f"  [{i}] REJECTED: missing blob (batch requires both filepath AND blob)")
            sys.exit(2)
        if not edit.get("filepath"):
            print(f"  [{i}] REJECTED: missing filepath (batch requires both filepath AND blob)")
            sys.exit(2)

    results = []
    for i, edit in enumerate(edits):
        try:
            filepath = edit.get("filepath")
            blob = edit.get("blob")
            edit_type = edit.get("type")
            if not filepath or not edit_type or not blob:
                results.append({"edit_index": i, "status": "error",
                                "message": "missing filepath, blob, or type"})
                continue

            try:
                blob_hash = _resolve_target(filepath, blob)
            except FileNotFoundError as e:
                results.append({"edit_index": i, "status": "error", "message": str(e)})
                continue
            except BlobMismatchError as e:
                # BUG-3 fix: surface as an error (not a conflict) with the
                # specific never-issued / wrong-file message.
                results.append({"edit_index": i, "status": "error", "message": str(e)})
                continue

            search_root = os.path.dirname(os.path.abspath(filepath))

            if edit_type == "replace":
                content = edit.get("content", "")
                line_range = edit.get("line_range")
                if not line_range:
                    results.append({"edit_index": i, "status": "error",
                                    "message": "missing line_range for replace"})
                    continue
                if content and not content.endswith('\n'):
                    content += '\n'
                tmp_path = _write_temp(content)
                try:
                    res = do_replace(blob_hash, line_range, tmp_path, search_root=search_root)
                    res["edit_index"] = i
                    results.append(res)
                finally:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)

            elif edit_type == "insert":
                line_str = edit.get("line_str")
                content = edit.get("content", "")
                if not line_str:
                    results.append({"edit_index": i, "status": "error",
                                    "message": "missing line_str for insert"})
                    continue
                line_no = int(line_str)
                if content and not content.endswith('\n'):
                    content += '\n'
                tmp_path = _write_temp(content)
                try:
                    res = do_replace(blob_hash, f"{line_no}-{line_no - 1}", tmp_path, search_root=search_root)
                    res["edit_index"] = i
                    results.append(res)
                finally:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)

            elif edit_type == "delete":
                line_range = edit.get("line_range")
                if not line_range:
                    results.append({"edit_index": i, "status": "error",
                                    "message": "missing line_range for delete"})
                    continue
                res = do_replace(blob_hash, line_range, os.devnull, search_root=search_root)
                res["edit_index"] = i
                results.append(res)

            else:
                results.append({"edit_index": i, "status": "error",
                                "message": f"unknown type: {edit_type}"})

        except Exception as e:
            results.append({"edit_index": i, "status": "error", "message": str(e)})

    # Print results — minimal output per spec
    ok_count = sum(1 for r in results if r.get("status") in ("ok", "auto_merged"))
    err_count = sum(1 for r in results if r.get("status") == "error")
    conflict_count = sum(1 for r in results if r.get("status") == "conflict")

    for r in results:
        idx = r.get("edit_index", "?")
        status = r.get("status", "unknown")
        if status in ("ok", "auto_merged"):
            print(f"  [{idx}] ok")
        elif status == "conflict":
            # Simple conflict message per spec — no diff dump
            print(f"  [{idx}] Merge conflict detected. Please read the latest version and try again.")
        else:
            print(f"  [{idx}] error: {r.get('message', '?')}")

    print(f"---")
    print(f"batch: {ok_count} ok, {conflict_count} conflict, {err_count} error ({len(edits)} total)")

    if conflict_count > 0:
        sys.exit(1)
    elif err_count > 0:
        sys.exit(2)


# ---------------------------------------------------------------------------
# Command: diff
# ---------------------------------------------------------------------------

def cmd_diff(args: list[str]) -> None:
    """vcs diff <filepath> <blob>"""
    if len(args) < 2:
        _error("usage: vcs diff <filepath> <blob>")

    filepath = args[0]
    blob = args[1]

    if not os.path.exists(filepath):
        _error(f"file not found: {filepath}")

    # Look up the snapshot by blob
    base = load_snapshot(blob)
    if base is None:
        _error(f"no snapshot found for blob '{_short_blob(blob)}'. Did you `vcs read` first?")

    with open(filepath, "r", encoding="utf-8", errors="replace", newline="") as fh:
        current = fh.read()

    base_lines = base.splitlines(keepends=True)
    current_lines = current.splitlines(keepends=True)

    base_numbered = [f"{i + 1}: {line}" for i, line in enumerate(base_lines)]
    current_numbered = [f"{i + 1}: {line}" for i, line in enumerate(current_lines)]

    diff_text = "".join(
        difflib.unified_diff(
            base_numbered,
            current_numbered,
            fromfile=f"blob:{_short_blob(blob)}",
            tofile=str(filepath),
        )
    )

    if not diff_text:
        print(f"blob: {_short_blob(blob)}")
        print(f"path: {filepath}")
        print("no changes")
    else:
        print(f"blob: {_short_blob(blob)}")
        print(f"path: {filepath}")
        print("---")
        print(diff_text, end="")


# ---------------------------------------------------------------------------
# Command: skeleton
# ---------------------------------------------------------------------------

def cmd_skeleton(args: list[str]) -> None:
    """vcs skeleton <filepath> [start-end]"""
    if not args:
        _error("usage: vcs skeleton <filepath> [start-end]")

    filepath = args[0]
    range_str = args[1] if len(args) > 1 else None

    if not os.path.exists(filepath):
        _error(f"file not found: {filepath}")

    # Parse optional range
    r_start, r_end = None, None
    if range_str:
        try:
            r_start, r_end = _parse_range_arg(range_str)
        except ValueError:
            _error(f"invalid range: '{range_str}'")

    # OPT-1 fix: call the skeletonizer in-process instead of spawning a
    # subprocess. Saves the ~80ms Python interpreter startup cost on every
    # skeleton call and removes the dependency on sys.executable.
    try:
        # Import skeleton module lazily so cold-start of `vcs read` (which
        # doesn't need skeleton) stays fast.
        import skeleton as _skeleton_mod
        skel_data = _skeleton_mod.generate_skeleton(
            Path(filepath), start=r_start, end=r_end
        )
    except FileNotFoundError as e:
        _error(str(e))
    except IsADirectoryError as e:
        _error(str(e))
    except ValueError as e:
        # Binary file or other validation error from skeleton
        _error(f"skeleton failed: {e}")
    except Exception as e:
        _error(f"skeleton failed: {type(e).__name__}: {e}")

    # Also register the blob so the user can edit
    try:
        blob_hash = _resolve_target(filepath)
    except FileNotFoundError as e:
        _error(str(e))
    except BlobMismatchError as e:
        _error(str(e))

    # Determine display range (defaults to 1..total_lines)
    total_lines = skel_data.get("total_lines", 0)
    start = r_start if r_start is not None else 1
    end = r_end if r_end is not None else total_lines

    blob = _short_blob(blob_hash)
    print(f"blob: {blob}")
    print(f"path: {filepath}")
    print(f"Code Lines: {start} to {end}")
    print("---")
    print(skel_data["output"], end="")


# ---------------------------------------------------------------------------
# Command: list
# ---------------------------------------------------------------------------

def cmd_list(args: list[str]) -> None:
    """vcs list [path] [--depth N] [--all]"""
    script = os.path.join(SCRIPT_DIR, "list.py")
    if not os.path.exists(script):
        _error("list.py not found")

    # Pass all args through to list.py
    cmd = [sys.executable, script] + args

    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
        print(out, end="")
    except subprocess.CalledProcessError as e:
        print(e.output, end="", file=sys.stderr)
        sys.exit(2)


# ---------------------------------------------------------------------------
# Command: grep
# ---------------------------------------------------------------------------

def cmd_grep(args: list[str]) -> None:
    """vcs grep <query> [path] [-i] [-r]"""
    if not args:
        _error("usage: vcs grep <query> [path] [-i] [-r]")

    script = os.path.join(SCRIPT_DIR, "grep.py")
    if not os.path.exists(script):
        _error("grep.py not found")

    cmd = [sys.executable, script] + args

    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.PIPE)
        if out.strip():
            print(out, end="")
            # Print match count from stderr if available
            return
    except subprocess.CalledProcessError:
        pass

    # Fallback to standard grep
    query = args[0]
    path = args[1] if len(args) > 1 and not args[1].startswith("-") else "."
    flags = [a for a in args[1:] if a.startswith("-")]

    fallback_cmd = ["grep", "-rn", "--color=never"]
    if "-i" in flags:
        fallback_cmd.append("-i")
    if "-r" in flags:
        fallback_cmd.append("-E")
    fallback_cmd.extend(["--", query, path])

    try:
        out = subprocess.check_output(fallback_cmd, text=True, stderr=subprocess.DEVNULL)
        print(f"--- standard grep (no context) ---")
        print(out, end="")
    except subprocess.CalledProcessError:
        print("no matches found", file=sys.stderr)
        sys.exit(0)


# ---------------------------------------------------------------------------
# Command: fmt
# ---------------------------------------------------------------------------

def cmd_fmt(args: list[str]) -> None:
    """vcs fmt [--check] [path]"""
    script = os.path.join(SCRIPT_DIR, "fmt.sh")
    if not os.path.exists(script):
        _error("fmt.sh not found")

    check_mode = "--check" in args
    remaining = [a for a in args if a != "--check"]
    path = remaining[0] if remaining else "."

    cmd = ["bash", script]
    if check_mode:
        cmd.append("--check")

    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT, cwd=path)
        print(out, end="")
    except subprocess.CalledProcessError as e:
        print(e.output, end="", file=sys.stderr)
        sys.exit(2)


# ---------------------------------------------------------------------------
# Command: test
# ---------------------------------------------------------------------------

def cmd_test(args: list[str]) -> None:
    """vcs test <command> [path]"""
    if not args:
        _error("usage: vcs test <command> [args...]")

    script = os.path.join(SCRIPT_DIR, "test.sh")
    if not os.path.exists(script):
        _error("test.sh not found")

    cmd = ["bash", script] + args

    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
        print(out, end="")
    except subprocess.CalledProcessError as e:
        print(e.output or "", end="")
        sys.exit(e.returncode or 2)


# ---------------------------------------------------------------------------
# Command: status
# ---------------------------------------------------------------------------

def cmd_status(args: list[str]) -> None:
    """vcs status [--prune]

    Without flags: list all blob→filepath mappings (existing files marked ✓,
    deleted files marked ✗).

    With --prune: run garbage collection BEFORE listing. Removes:
      - Registry entries whose file no longer exists on disk
      - Orphan snapshot files (no corresponding registry entry, left over
        from v2.0 short-prefix duplicates)
    Prints a summary of what was pruned, then the post-gc listing.
    """
    try:
        repo_root = _find_repo_root()

        # OPT-3 fix: --prune flag triggers gc_store() before listing
        if "--prune" in args:
            result = gc_store(repo_root=repo_root, prune_stale=True, prune_duplicates=True)
            print(f"pruned: {result['stale_entries']} stale entries, "
                  f"{result['orphan_snapshots']} orphan snapshots")
            print(f"remaining: {result['total_remaining']} blobs")
            print("---")

        data = _load_store(repo_root)
        blobs = data.get("blobs", {})

        if not blobs:
            print("no blobs registered")
            return

        print(f"repo: {repo_root}")
        print(f"blobs: {len(blobs)}")
        print("---")
        for blob_hash, filepath in sorted(blobs.items(), key=lambda x: x[1]):
            short = blob_hash[:8]
            exists = "✓" if os.path.exists(os.path.join(repo_root, filepath)) else "✗"
            print(f"  {exists} {short}  →  {filepath}")
    except Exception as e:
        _error(str(e))


# ---------------------------------------------------------------------------
# Command: gc  (OPT-3 fix — explicit garbage collection)
# ---------------------------------------------------------------------------

def cmd_gc(args: list[str]) -> None:
    """vcs gc

    Garbage-collect the .vcs_store.json registry and .vcs_snapshots/ directory.

    Removes:
      - Registry entries whose filepath no longer exists on disk (e.g. files
        deleted via `vcs delete` or externally — these otherwise linger
        forever, BUG-4 finding #3).
      - Orphan snapshot files in .vcs_snapshots/ that have no corresponding
        registry entry (left over from v2.0's short-prefix duplicate bug).

    Note: the per-file blob cap (MAX_BLOBS_PER_FILE=100) is enforced
    automatically at register() time, so `vcs gc` doesn't need to handle
    that case — it only handles the two stale-entry cases above.

    Output:
        pruned: N stale entries, M orphan snapshots
        remaining: K blobs
    """
    try:
        repo_root = _find_repo_root()
        result = gc_store(repo_root=repo_root, prune_stale=True, prune_duplicates=True)
        print(f"pruned: {result['stale_entries']} stale entries, "
              f"{result['orphan_snapshots']} orphan snapshots")
        print(f"remaining: {result['total_remaining']} blobs")
    except Exception as e:
        _error(str(e))


# ---------------------------------------------------------------------------
# Edit result printer — clean per v2 spec
# ---------------------------------------------------------------------------

def _print_edit_result(result: dict) -> None:
    """Print a human-readable edit result.

    Per v2 spec:
      - success (ok or auto_merged) → just `status: ok`
      - conflict → simple human message: "Merge conflict detected. Please read
        the latest version and try again."
      - error → status: error + message
    """
    status = result.get("status", "unknown")

    if status in ("ok", "auto_merged"):
        # Clean output: just `status: ok`
        print("status: ok")
        sys.exit(0)

    elif status == "conflict":
        # Simple conflict message — no diff, no conflicting lines, no technical details
        print("Merge conflict detected. Please read the latest version and try again.")
        sys.exit(1)

    else:
        print(f"status: error")
        print(f"message: {result.get('message', 'unknown error')}")
        sys.exit(2)


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

COMMANDS = {
    "read": cmd_read,
    "replace": cmd_replace,
    "insert": cmd_insert,
    "delete": cmd_delete,
    "create": cmd_create,
    "batch": cmd_batch,
    "diff": cmd_diff,
    "skeleton": cmd_skeleton,
    "list": cmd_list,
    "grep": cmd_grep,
    "fmt": cmd_fmt,
    "test": cmd_test,
    "status": cmd_status,
    "gc": cmd_gc,
}

USAGE = """\
usage: vcs <command> [args...]

commands:
  read      <filepath> [start-end]                  Read file with line numbers + blob hash
  replace   <filepath> <blob> <start-end> <<'EOF'   Replace line range (stdin = new content)
  insert    <filepath> <blob> <line> <<'EOF'        Insert before line (stdin = content)
  delete    <filepath> <blob> <start-end>           Delete line range
  delete    <filepath>                              Delete file or entire directory
  create    <filepath> <<'EOF'                      Create new file with content (stdin)
  batch     <<'EOF'                                 Batch edits (JSON array, BOTH filepath+blob per edit)
  diff      <filepath> <blob>                       Unified diff: blob snapshot vs disk
  skeleton  <filepath> [start-end]                  Collapsed structure view
  list      [path] [--depth N] [--all]              Directory list (.gitignore aware, capped)
  grep      <query> [path] [-i] [-r]                Search with function/class context
  fmt       [--check] [path]                        Auto-format staged files
  test      <command> [args...]                     Run tests, show failures only
  status    [--prune]                               List blob→filepath mappings (or prune stale entries)
  gc                                            Garbage-collect stale registry entries + orphan snapshots

notes:
  <blob>     = blob hash from `vcs read` (proves you read the file)
  <filepath> = path to the file (confirms which file)
  BOTH are required for replace / insert / delete(line) / batch — not just one.
  heredoc:    vcs replace myfile.py <blob> 8-50 << 'EOF'
              new code here
              EOF
"""


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(USAGE)
        sys.exit(0)

    if sys.argv[1] in ("-V", "--version"):
        print("vcs 2.1.0")
        sys.exit(0)

    command = sys.argv[1]
    args = sys.argv[2:]

    handler = COMMANDS.get(command)
    if handler is None:
        print(f"error: unknown command '{command}'", file=sys.stderr)
        print(f"run 'vcs --help' for usage", file=sys.stderr)
        sys.exit(2)

    handler(args)


if __name__ == "__main__":
    main()
