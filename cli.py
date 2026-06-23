#!/usr/bin/env python3
"""VCS Edit Tool — Rich CLI for AI agents.

A blob-hash-based file editing CLI with heredoc support, rich output,
and 3-way merge conflict resolution.

Commands:
    vcs read <filepath> [start-end]
    vcs replace <target> <start-end> << 'EOF' ... EOF
    vcs insert <target> <line> << 'EOF' ... EOF
    vcs delete <target> <start-end>
    vcs batch << 'EOF' [...json...] EOF
    vcs diff <target>
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
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

# Allow running as a script (python cli.py) and as an installed entry point.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.read import read_file, MAX_READ_LINES
from core.replace import replace as do_replace
from core.blob import get_blob_hash
from core.store import (
    register, resolve_path, save_snapshot, load_snapshot,
    _find_repo_root, _load_store,
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


def _resolve_target(target: str) -> str:
    """If target is an existing filepath, snapshot it and return its blob hash.
    Otherwise assume it's a blob hash and return it.
    """
    if os.path.exists(target):
        blob = get_blob_hash(target)
        register(blob, target)
        with open(target, "r", encoding="utf-8", errors="replace", newline="") as fh:
            save_snapshot(blob, fh.read())
        return blob
    return target


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
    except Exception as e:
        _error(f"{type(e).__name__}: {e}")

    # Auto-fallback to skeleton for >800 lines with no range specified
    if end is None and result.get("total_lines", 0) > 800:
        try:
            script = os.path.join(SCRIPT_DIR, "skeleton.py")
            if os.path.exists(script):
                cmd = [sys.executable, script, filepath, "--json"]
                out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
                skel_data = json.loads(out)
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
    print(f"blob: {blob} (Use this for any further edits, no need to read again)")
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
# Command: replace
# ---------------------------------------------------------------------------

def cmd_replace(args: list[str]) -> None:
    """vcs replace <target> <start-end> << 'EOF' ... EOF"""
    if len(args) < 2:
        _error("usage: vcs replace <target> <start-end> << 'EOF'\\nnew content\\nEOF")

    target = args[0]
    line_range = args[1]
    content = _read_stdin()

    if content and not content.endswith('\n'):
        content += '\n'

    blob_hash = _resolve_target(target)
    tmp_path = _write_temp(content)

    try:
        search_root = os.path.dirname(os.path.abspath(target)) if os.path.exists(target) else "."
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
# Command: replace-text
# ---------------------------------------------------------------------------

def cmd_replace_text(args: list[str]) -> None:
    """vcs replace-text <target> << 'EOF' ..."""
    if len(args) < 1:
        _error("usage: vcs replace-text <target> << 'EOF'\\n<<<< TARGET\\n...\\n====\\n...\\n>>>>\\nEOF")

    target = args[0]
    content = _read_stdin()

    blob_hash = _resolve_target(target)
    search_root = os.path.dirname(os.path.abspath(target)) if os.path.exists(target) else "."
    
    if "<<<< TARGET\n" not in content or "====\n" not in content or ">>>>" not in content:
        _error("invalid format. must contain <<<< TARGET, ====, and >>>>")

    target_block = content.split("<<<< TARGET\n")[1].split("====\n")[0]
    new_block = content.split("====\n")[1].split(">>>>")[0]
    
    from core.store import resolve_path
    filepath = resolve_path(blob_hash, search_root=search_root)
    if not filepath:
        _error(f"blob hash '{blob_hash}' not found")
        
    with open(filepath, "r", encoding="utf-8") as f:
        file_content = f.read()
        
    if file_content.count(target_block) == 0:
        _error("TARGET block not found in file")
    elif file_content.count(target_block) > 1:
        _error("TARGET block found multiple times in file. Be more specific.")
        
    new_file_content = file_content.replace(target_block, new_block)
    
    tmp_path = _write_temp(new_file_content)
    try:
        from core.replace import replace as do_replace
        total_lines = len(file_content.splitlines())
        res = do_replace(blob_hash, f"1-{total_lines}", tmp_path, search_root=search_root)
    except Exception as e:
        _error(f"{type(e).__name__}: {e}")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    _print_edit_result(res)

# ---------------------------------------------------------------------------
# Command: insert
# ---------------------------------------------------------------------------

def cmd_insert(args: list[str]) -> None:
    """vcs insert <target> <line> << 'EOF' ... EOF"""
    if len(args) < 2:
        _error("usage: vcs insert <target> <line> << 'EOF'\\ncontent\\nEOF")

    target = args[0]
    line_str = args[1]

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

    blob_hash = _resolve_target(target)
    tmp_path = _write_temp(content)

    try:
        search_root = os.path.dirname(os.path.abspath(target)) if os.path.exists(target) else "."
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
# Command: delete
# ---------------------------------------------------------------------------

def cmd_delete(args: list[str]) -> None:
    """vcs delete <target> <start-end>"""
    if len(args) < 2:
        _error("usage: vcs delete <target> <start-end>")

    target = args[0]
    line_range = args[1]

    blob_hash = _resolve_target(target)

    try:
        search_root = os.path.dirname(os.path.abspath(target)) if os.path.exists(target) else "."
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


# ---------------------------------------------------------------------------
# Command: batch
# ---------------------------------------------------------------------------

def cmd_batch(args: list[str]) -> None:
    """vcs batch << 'EOF' [...json...] EOF"""
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
                if len(parts) >= 5:
                    op = parts[1].lower()
                    target = parts[2]
                    rng = parts[3]
                    current_edit = {"type": op, "target": target}
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

    results = []
    for i, edit in enumerate(edits):
        try:
            target = edit.get("target")
            edit_type = edit.get("type")
            if not target or not edit_type:
                results.append({"edit_index": i, "status": "error", "message": "missing target or type"})
                continue

            blob_hash = _resolve_target(target)
            search_root = os.path.dirname(os.path.abspath(target)) if os.path.exists(target) else "."

            if edit_type == "replace":
                content = edit.get("content", "")
                line_range = edit.get("line_range")
                if not line_range:
                    results.append({"edit_index": i, "status": "error", "message": "missing line_range for replace"})
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
                    results.append({"edit_index": i, "status": "error", "message": "missing line_str for insert"})
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
                    results.append({"edit_index": i, "status": "error", "message": "missing line_range for delete"})
                    continue
                res = do_replace(blob_hash, line_range, os.devnull, search_root=search_root)
                res["edit_index"] = i
                results.append(res)

            else:
                results.append({"edit_index": i, "status": "error", "message": f"unknown type: {edit_type}"})

        except Exception as e:
            results.append({"edit_index": i, "status": "error", "message": str(e)})

    # Print results
    ok_count = sum(1 for r in results if r.get("status") in ("ok", "auto_merged"))
    err_count = sum(1 for r in results if r.get("status") == "error")
    conflict_count = sum(1 for r in results if r.get("status") == "conflict")

    for r in results:
        idx = r.get("edit_index", "?")
        status = r.get("status", "unknown")
        if status in ("ok", "auto_merged"):
            print(f"  [{idx}] {status}")
        elif status == "conflict":
            print(f"  [{idx}] CONFLICT: {r.get('conflicting_lines', '?')}")
        else:
            print(f"  [{idx}] ERROR: {r.get('message', '?')}")

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
    """vcs diff <target>"""
    if not args:
        _error("usage: vcs diff <target>")

    target = args[0]
    blob_hash = _resolve_target(target)
    filepath = resolve_path(blob_hash)

    if filepath is None:
        _error(f"blob hash '{blob_hash[:8]}' not found in registry or repo")

    base = load_snapshot(blob_hash)
    if base is None:
        _error(f"no snapshot found for blob '{blob_hash[:8]}'. Did you `vcs read` first?")

    if not os.path.exists(filepath):
        _error(f"file not found: {filepath}")

    with open(filepath, "r", encoding="utf-8", errors="replace", newline="") as fh:
        current = fh.read()

    base_lines = base.splitlines(keepends=True)
    current_lines = current.splitlines(keepends=True)

    # Show diff with line numbers embedded
    base_numbered = [f"{i + 1}: {line}" for i, line in enumerate(base_lines)]
    current_numbered = [f"{i + 1}: {line}" for i, line in enumerate(current_lines)]

    diff_text = "".join(
        difflib.unified_diff(
            base_numbered,
            current_numbered,
            fromfile=f"blob:{_short_blob(blob_hash)}",
            tofile=str(filepath),
        )
    )

    if not diff_text:
        print(f"blob: {_short_blob(blob_hash)}")
        print(f"path: {filepath}")
        print("no changes")
    else:
        print(f"blob: {_short_blob(blob_hash)}")
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

    script = os.path.join(SCRIPT_DIR, "skeleton.py")
    if not os.path.exists(script):
        _error("skeleton.py not found")

    cmd = [sys.executable, script, filepath]

    if range_str:
        try:
            start, end = _parse_range_arg(range_str)
            if start is not None:
                cmd.extend(["--start", str(start)])
            if end is not None:
                cmd.extend(["--end", str(end)])
        except ValueError:
            _error(f"invalid range: '{range_str}'")

    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        _error(f"skeleton failed: {e.stderr.strip() if e.stderr else 'unknown error'}")

    # Also register the blob so the user can edit
    blob_hash = _resolve_target(filepath)

    start = 1
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
            total_lines = len(fh.read().splitlines())
    except Exception:
        total_lines = 0
    end = total_lines

    if range_str:
        try:
            r_start, r_end = _parse_range_arg(range_str)
            if r_start is not None:
                start = r_start
            if r_end is not None:
                end = r_end
        except ValueError:
            pass

    blob = _short_blob(blob_hash)
    print(f"blob: {blob} (Use this for any further edits, no need to read again)")
    print(f"path: {filepath}")
    print(f"Code Lines: {start} to {end}")
    print("---")
    print(out, end="")


# ---------------------------------------------------------------------------
# Command: tree
# ---------------------------------------------------------------------------

def cmd_tree(args: list[str]) -> None:
    """vcs tree [path] [--depth N] [--all]"""
    script = os.path.join(SCRIPT_DIR, "tree.py")
    if not os.path.exists(script):
        _error("tree.py not found")

    # Pass all args through to tree.py
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
    # Parse args manually to extract query and flags
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
    """vcs status"""
    try:
        repo_root = _find_repo_root()
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
# Edit result printer (shared by replace/insert/delete)
# ---------------------------------------------------------------------------

def _print_edit_result(result: dict) -> None:
    """Print a human-readable edit result."""
    status = result.get("status", "unknown")

    if status in ("ok", "auto_merged"):
        print(f"status: {status}")
        if status == "auto_merged":
            regions = result.get("merged_regions", [])
            if regions:
                print(f"merged_regions: {len(regions)}")
                for r in regions:
                    print(f"  lines {r.get('start', '?')}-{r.get('end', '?')}")
        sys.exit(0)

    elif status == "conflict":
        print(f"status: CONFLICT")
        print(f"conflicting_lines: {result.get('conflicting_lines', '?')}")
        if result.get("message"):
            print(f"message: {result['message']}")
        if result.get("diff"):
            print("---")
            print(result["diff"], end="")
        if result.get("base_content"):
            print("--- base ---")
            _print_numbered(result["base_content"])
        if result.get("their_change"):
            print("--- theirs (current on disk) ---")
            _print_numbered(result["their_change"])
        if result.get("your_change"):
            print("--- yours (your edit) ---")
            _print_numbered(result["your_change"])
        sys.exit(1)

    else:
        print(f"status: error")
        print(f"message: {result.get('message', 'unknown error')}")
        sys.exit(2)


def _print_numbered(content: str) -> None:
    """Print content with line numbers if not already numbered."""
    lines = content.splitlines(keepends=True)
    for i, line in enumerate(lines, 1):
        # Check if already numbered (pattern: "N: ")
        stripped = line.lstrip()
        if stripped and stripped[0].isdigit() and ": " in stripped[:10]:
            print(line, end="")
        else:
            print(f"{i}: {line}", end="")


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

COMMANDS = {
    "read": cmd_read,
    "replace": cmd_replace,
    "replace-text": cmd_replace_text,
    "insert": cmd_insert,
    "delete": cmd_delete,
    "batch": cmd_batch,
    "diff": cmd_diff,
    "skeleton": cmd_skeleton,
    "tree": cmd_tree,
    "grep": cmd_grep,
    "fmt": cmd_fmt,
    "test": cmd_test,
    "status": cmd_status,
}

USAGE = """\
usage: vcs <command> [args...]

commands:
  read      <filepath> [start-end]           Read file with line numbers + blob hash
  replace   <target> <start-end> <<'EOF'     Replace line range (stdin = new content)
  insert    <target> <line> <<'EOF'          Insert before line (stdin = content)
  delete    <target> <start-end>             Delete line range
  batch     <<'EOF'                          Batch edits from JSON array on stdin
  diff      <target>                         Unified diff: blob snapshot vs disk
  skeleton  <filepath> [start-end]           Collapsed structure view
  tree      [path] [--depth N] [--all]       Directory tree (.gitignore aware)
  grep      <query> [path] [-i] [-r]         Search with function/class context
  fmt       [--check] [path]                 Auto-format staged files
  test      <command> [args...]              Run tests, show failures only
  status                                     List all blob→filepath mappings

notes:
  <target> = blob hash (from vcs read) OR filepath
  heredoc:  vcs replace myfile.py 8-50 << 'EOF'
            new code here
            EOF
"""


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(USAGE)
        sys.exit(0)

    if sys.argv[1] in ("-V", "--version"):
        print("vcs 1.0.0")
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
