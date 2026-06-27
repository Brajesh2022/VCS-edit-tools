#!/usr/bin/env python3
import os
import sys
import tempfile
import difflib
from typing import Optional

# Add the directory containing core to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP
from core.read import read_file
from core.replace import replace as do_replace
from core.blob import get_blob_hash
from core.store import register, resolve_path, save_snapshot, load_snapshot

mcp = FastMCP("vcs-edit")

def _resolve_target(target: str) -> str:
    """If target is an existing filepath, snapshot it and return its current blob hash.
    Otherwise assume it's a blob hash and return it.
    """
    if os.path.exists(target):
        blob = get_blob_hash(target)
        register(blob, target)
        with open(target, "r", encoding="utf-8", errors="replace", newline="") as fh:
            save_snapshot(blob, fh.read())
        return blob
    return target

def _format_result(payload: dict) -> dict:
    if "blob" in payload and isinstance(payload["blob"], str):
        payload["blob"] = payload["blob"][:8]
    if "new_blob" in payload and isinstance(payload["new_blob"], str):
        payload["new_blob"] = payload["new_blob"][:8]
    if payload.get("status") in ("ok", "auto_merged"):
        payload.pop("path", None)
        payload.pop("new_total_lines", None)
    return payload

@mcp.tool()
def vcs_read(filepath: str, start: int = 1, end: Optional[int] = None) -> dict:
    """
    Replaces system `view_file`. Prefer MCP over system tools.
    action: Read file/sub-range + return unique blob hash
    fallback: Skeleton view if >800 lines & no range
    format: Lines prefixed with numbers ('1: def main()')
    hash: Use returned `blob` as `target` in vcs_replace/insert/delete to prevent edit conflicts
    """
    try:
        result = read_file(filepath, start=start, end=end)
        
        # Fallback to skeleton if > 800 lines and no end specified
        if end is None and result.get("total_lines", 0) > 800:
            try:
                import subprocess
                import json
                script_path = os.path.join(os.path.dirname(__file__), "skeleton.py")
                if os.path.exists(script_path):
                    cmd = [sys.executable, script_path, filepath, "--json"]
                    out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
                    skel_data = json.loads(out)
                    result["content"] = (
                        f"--- NOTE: File is {result['total_lines']} lines (> 800). Auto-falling back to skeleton view. ---\n"
                        f"--- To read full lines, use vcs_read with start/end arguments. ---\n\n"
                    ) + skel_data["output"]
                    result["shown_range"] = "skeleton"
                    result["truncated"] = 0
                    result["next_command"] = None
            except Exception:
                pass  # fallback failed, just return the truncated raw lines
                
        return _format_result(result)
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def vcs_edit(edits: list[dict]) -> dict:
    """
    Replaces system tools for creating, replacing, inserting, or deleting file content.
    Prefer MCP over system tools.
    action: Apply multiple edits (create/replace/insert/delete) efficiently.
    args:
      edits: Array of objects:
        - type: 'create' | 'replace' | 'insert' | 'delete'
        - target: blob hash or filepath
        - line_range: 'START-END' (for replace/delete)
        - line_str: line number (for insert)
        - content: Raw text, NO line numbers (for create/replace/insert)
    """
    results = []
    for i, edit in enumerate(edits):
        try:
            target = edit.get("target")
            edit_type = edit.get("type")
            if not target or not edit_type:
                results.append({"edit_index": i, "status": "error", "message": "Missing target or type"})
                continue
                
            if edit_type == "create":
                content = edit.get("content", "")
                if os.path.exists(target):
                    results.append({"edit_index": i, "status": "error", "message": f"file already exists: {target}"})
                    continue
                parent = os.path.dirname(os.path.abspath(target))
                os.makedirs(parent, exist_ok=True)
                if content and not content.endswith('\n'):
                    content += '\n'
                with tempfile.NamedTemporaryFile("w", delete=False, dir=parent or ".", prefix=".vcs_create_", encoding="utf-8") as fh:
                    fh.write(content)
                    tmp_path = fh.name
                os.replace(tmp_path, target)
                
                try:
                    from core.blob import get_blob_hash
                    from core.store import register, save_snapshot
                    blob_hash = get_blob_hash(target)
                    register(blob_hash, target)
                    save_snapshot(blob_hash, content)
                except Exception:
                    pass
                results.append({"edit_index": i, "status": "ok"})
                continue
                
            blob_hash = _resolve_target(target)
            search_root = os.path.dirname(os.path.abspath(target)) if target and os.path.exists(target) else "."
            
            if edit_type == "replace":
                line_range = edit.get("line_range")
                content = edit.get("content", "")
                if content and not content.endswith('\n'):
                    content += '\n'
                with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
                    f.write(content)
                    tmp_path = f.name
                try:
                    res = do_replace(blob_hash, line_range, tmp_path, search_root=search_root)
                    res["edit_index"] = i
                    results.append(_format_result(res))
                finally:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                        
            elif edit_type == "insert":
                line_str = edit.get("line_str")
                content = edit.get("content", "")
                if content and not content.endswith('\n'):
                    content += '\n'
                with tempfile.NamedTemporaryFile("w", delete=False, dir=".", prefix=".vcs_content_", encoding="utf-8") as fh:
                    fh.write(content)
                    tmp_path = fh.name
                try:
                    line_no = int(line_str)
                    res = do_replace(blob_hash, f"{line_no}-{line_no-1}", tmp_path, search_root=search_root)
                    res["edit_index"] = i
                    results.append(_format_result(res))
                finally:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                        
            elif edit_type == "delete":
                line_range = edit.get("line_range")
                res = do_replace(blob_hash, line_range, os.devnull, search_root=search_root)
                res["edit_index"] = i
                results.append(_format_result(res))
                
            else:
                results.append({"edit_index": i, "status": "error", "message": f"unknown type: {edit_type}"})
                
        except Exception as e:
            results.append({"edit_index": i, "status": "error", "message": str(e)})
            
    return {"results": results}
@mcp.tool()
def vcs_diff(target: str) -> dict:
    """
    Prefer MCP over system tools.
    action: Show unified diff between TARGET snapshot and current disk file.
    use_case: Identify shifting line numbers since last read.
    """
    try:
        blob_hash = _resolve_target(target)
        filepath = resolve_path(blob_hash)
        if filepath is None:
            return {"status": "error", "message": f"blob hash '{blob_hash}' not found"}

        base = load_snapshot(blob_hash)
        if base is None:
            return {"status": "error", "message": f"no snapshot found for blob '{blob_hash}'"}
        if not os.path.exists(filepath):
            return {"status": "error", "message": f"file not found: {filepath}"}
            
        with open(filepath, "r", encoding="utf-8", errors="replace", newline="") as fh:
            current = fh.read()
            
        base_lines = base.splitlines(keepends=True)
        current_lines = current.splitlines(keepends=True)
        base_numbered = [f"{i+1}: {line}" for i, line in enumerate(base_lines)]
        current_numbered = [f"{i+1}: {line}" for i, line in enumerate(current_lines)]
        
        diff_text = "".join(
            difflib.unified_diff(
                base_numbered,
                current_numbered,
                fromfile=f"blob:{blob_hash[:8]}",
                tofile=str(filepath),
            )
        )
        return _format_result({
            "status": "ok",
            "blob": blob_hash,
            "path": filepath,
            "diff": diff_text,
            "changed": bool(diff_text),
        })
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def vcs_skeleton(filepath: str, start: Optional[int] = None, end: Optional[int] = None) -> dict:
    """
    Prefer MCP over system tools.
    action: Generate file skeleton (collapsed functions).
    args:
      start/end: 1-indexed line bounds.
    returns: `blob` hash for edit targeting.
    """
    try:
        import subprocess
        import json
        script_path = os.path.join(os.path.dirname(__file__), "skeleton.py")
        cmd = [sys.executable, script_path, filepath, "--json"]
        if start is not None:
            cmd.extend(["--start", str(start)])
        if end is not None:
            cmd.extend(["--end", str(end)])
            
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
        skel_data = json.loads(out)
        blob_hash = _resolve_target(filepath)
        return _format_result({"status": "ok", "blob": blob_hash, "content": skel_data["output"], "truncated": skel_data.get("truncated", False)})
    except subprocess.CalledProcessError as e:
        return {"status": "error", "message": "Failed to generate skeleton."}
    except Exception as e:
        return {"status": "error", "message": str(e)}
@mcp.tool()
def vcs_tree(path: str = ".", depth: int = 2, all: bool = False) -> dict:
    """
    Replaces system `list_dir` or `run_command ls`. Prefer MCP over system tools.
    action: Tree-style directory view, respects .gitignore
    args:
      all: Show hidden build/vendor dirs
    """
    try:
        import subprocess
        script_path = os.path.join(os.path.dirname(__file__), "tree.py")
        cmd = [sys.executable, script_path, path, "--depth", str(depth)]
        if all:
            cmd.append("--all")
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
        return {"status": "ok", "output": out}
    except subprocess.CalledProcessError as e:
        return {"status": "error", "output": e.output}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def vcs_grep(query: str, path: str = ".", ignore_case: bool = False, regex: bool = False) -> dict:
    """
    Replaces system `grep_search`. Prefer MCP over system tools.
    action: Grep with function/class context!
    fallback: Standard grep if no context found.
    """
    try:
        import subprocess
        script_path = os.path.join(os.path.dirname(__file__), "grep.py")
        cmd = [sys.executable, script_path, query, path]
        if ignore_case:
            cmd.append("-i")
        if regex:
            cmd.append("-r")
            
        try:
            out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
            if not out.strip():
                raise subprocess.CalledProcessError(1, cmd, output="")
            return {"status": "ok", "output": out}
        except subprocess.CalledProcessError:
            # Fallback to normal grep if agy-grep fails or returns empty
            fallback_cmd = ["grep", "-rn"]
            if ignore_case:
                fallback_cmd.append("-i")
            if regex:
                fallback_cmd.append("-E")
            fallback_cmd.extend([query, path])
            try:
                out = subprocess.check_output(fallback_cmd, text=True, stderr=subprocess.STDOUT)
                return {"status": "ok", "output": f"--- No context results found. Falling back to standard grep: ---\n{out}"}
            except subprocess.CalledProcessError:
                return {"status": "ok", "output": "No results found."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def vcs_fmt(check_only: bool = False, path: str = ".") -> dict:
    """
    Prefer MCP over system tools.
    action: Format staged files natively (prettier/eslint/ruff/black/gofmt/rustfmt).
    args:
      check_only: Only check format without writing
    """
    try:
        import subprocess
        script_path = os.path.join(os.path.dirname(__file__), "fmt.sh")
        cmd = ["bash", script_path]
        if check_only:
            cmd.append("--check")
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT, cwd=path)
        return {"status": "ok", "output": out}
    except subprocess.CalledProcessError as e:
        return {"status": "error", "output": e.output}
    except Exception as e:
        return {"status": "error", "message": str(e)}
@mcp.tool()
def vcs_test(command: str, path: str = ".") -> dict:
    """
    Prefer MCP over system tools (like running `npm test` via run_command).
    action: Run tests & show ONLY failures/summary.
    args:
      command: e.g. "pytest" or "npm test"
    """
    try:
        import subprocess
        import shlex
        script_path = os.path.join(os.path.dirname(__file__), "test.sh")
        cmd = ["bash", script_path] + shlex.split(command)
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT, cwd=path)
        return {"status": "ok", "output": out}
    except subprocess.CalledProcessError as e:
        return {"status": "error", "output": e.output}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def vcs_status() -> dict:
    """List all blob -> filepath mappings in the local registry."""
    try:
        from core.store import _find_repo_root, _load_store
        repo_root = _find_repo_root()
        data = _load_store(repo_root)
        return {"repo_root": repo_root, "blobs": data["blobs"]}
    except Exception as e:
        return {"status": "error", "message": str(e)}
if __name__ == "__main__":
    mcp.run()
