#!/usr/bin/env python3
import os
import sys
import tempfile
import difflib
import subprocess
from typing import Optional, List, Literal, Union
from typing_extensions import Annotated
from pydantic import BaseModel, Field

# Add the directory containing core to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP
from core.read import read_file
from core.replace import replace as do_replace
from core.blob import get_blob_hash
from core.store import register, resolve_path, save_snapshot, load_snapshot, _find_repo_root, _load_store, BlobMismatchError

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

def _write_temp(content: str, dir=".") -> str:
    if content and not content.endswith('\n'):
        content += '\n'
    fd, tmp_path = tempfile.mkstemp(dir=dir, prefix=".vcs_content_")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
        fh.write(content)
    return tmp_path

# --- Pydantic Schemas for Nested objects ---
class ReplaceOperation(BaseModel):
    action: Literal["replace"]
    filepath: str
    blob: str
    start_line: int
    end_line: int
    content: str

class InsertOperation(BaseModel):
    action: Literal["insert"]
    filepath: str
    blob: str
    line: int
    content: str

class DeleteOperation(BaseModel):
    action: Literal["delete"]
    filepath: str
    blob: str
    start_line: int
    end_line: int

class CreateOperation(BaseModel):
    action: Literal["create"]
    filepath: str
    content: str

EditOperation = Annotated[
    Union[ReplaceOperation, InsertOperation, DeleteOperation, CreateOperation], 
    Field(discriminator="action")
]

# --- Tools ---
@mcp.tool()
def vcs_read(filepath: str, start: int = 1, end: Optional[int] = None) -> dict:
    """Read a file or sub-range, returning a unique blob hash for atomic edits."""
    try:
        result = read_file(filepath, start=start, end=end)
        
        if end is None and result.get("total_lines", 0) > 800:
            try:
                script_path = os.path.join(os.path.dirname(__file__), "skeleton.py")
                if os.path.exists(script_path):
                    cmd = [sys.executable, script_path, filepath, "--json"]
                    out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
                    import json
                    skel_data = json.loads(out)
                    result["content"] = (
                        f"--- NOTE: File is {result['total_lines']} lines (> 800). Auto-falling back to skeleton view. ---\n"
                        f"--- To read full lines, use vcs_read with start/end arguments. ---\n\n"
                    ) + skel_data["output"]
                    result["shown_range"] = "skeleton"
                    result["truncated"] = 0
            except Exception:
                pass
                
        return _format_result(result)
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def vcs_replace(target: str, line_range: str, content: str) -> dict:
    """Replace a specific line range with new content."""
    try:
        blob_hash = _resolve_target(target)
        search_root = os.path.dirname(os.path.abspath(target)) if os.path.exists(target) else "."
        tmp_path = _write_temp(content, dir=search_root)
        try:
            result = do_replace(blob_hash, line_range, tmp_path, search_root=search_root)
            return _format_result(result)
        finally:
            if os.path.exists(tmp_path): os.remove(tmp_path)
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def vcs_insert(target: str, line: int, content: str) -> dict:
    """Insert text BEFORE a specified line."""
    try:
        blob_hash = _resolve_target(target)
        search_root = os.path.dirname(os.path.abspath(target)) if os.path.exists(target) else "."
        if line < 1: return {"status": "error", "message": f"line must be >= 1"}
        tmp_path = _write_temp(content, dir=search_root)
        try:
            result = do_replace(blob_hash, f"{line}-{line-1}", tmp_path, search_root=search_root)
            return _format_result(result)
        finally:
            if os.path.exists(tmp_path): os.remove(tmp_path)
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def vcs_delete(target: str, line_range: str) -> dict:
    """Delete a specific line range."""
    try:
        blob_hash = _resolve_target(target)
        search_root = os.path.dirname(os.path.abspath(target)) if os.path.exists(target) else "."
        result = do_replace(blob_hash, line_range, os.devnull, search_root=search_root)
        return _format_result(result)
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def vcs_create(filepath: str, content: str) -> dict:
    """Create a new file with content."""
    try:
        if os.path.exists(filepath):
            return {"status": "error", "message": f"file already exists: {filepath}"}
        parent = os.path.dirname(os.path.abspath(filepath))
        if parent:
            os.makedirs(parent, exist_ok=True)
        if content and not content.endswith('\n'):
            content += '\n'
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return {"status": "ok", "message": f"created {filepath}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def vcs_batch_edit(edits: list[EditOperation]) -> dict:
    """Apply multiple edits (replace/insert/delete/create) efficiently."""
    results = []
    
    for i, edit in enumerate(edits):
        try:
            if edit.action == "create":
                if os.path.exists(edit.filepath):
                    results.append({"edit_index": i, "status": "error", "message": "file already exists"})
                    continue
                parent = os.path.dirname(os.path.abspath(edit.filepath))
                if parent:
                    os.makedirs(parent, exist_ok=True)
                content = edit.content
                if content and not content.endswith('\n'):
                    content += '\n'
                with open(edit.filepath, "w", encoding="utf-8") as f:
                    f.write(content)
                results.append({"edit_index": i, "status": "ok", "message": f"created {edit.filepath}"})
                continue
            
            # Resolve blob for replace/insert/delete
            try:
                blob_hash = _resolve_target(edit.filepath) if not edit.blob else _resolve_target(edit.blob)
            except Exception as e:
                results.append({"edit_index": i, "status": "error", "message": str(e)})
                continue
                
            search_root = os.path.dirname(os.path.abspath(edit.filepath))
            if not search_root: search_root = "."
            
            if edit.action == "replace":
                tmp_path = _write_temp(edit.content, dir=search_root)
                try:
                    res = do_replace(blob_hash, f"{edit.start_line}-{edit.end_line}", tmp_path, search_root=search_root)
                    res["edit_index"] = i
                    results.append(_format_result(res))
                finally:
                    if os.path.exists(tmp_path): os.remove(tmp_path)
            
            elif edit.action == "insert":
                tmp_path = _write_temp(edit.content, dir=search_root)
                try:
                    res = do_replace(blob_hash, f"{edit.line}-{edit.line-1}", tmp_path, search_root=search_root)
                    res["edit_index"] = i
                    results.append(_format_result(res))
                finally:
                    if os.path.exists(tmp_path): os.remove(tmp_path)
                    
            elif edit.action == "delete":
                res = do_replace(blob_hash, f"{edit.start_line}-{edit.end_line}", os.devnull, search_root=search_root)
                res["edit_index"] = i
                results.append(_format_result(res))
                
        except Exception as e:
            results.append({"edit_index": i, "status": "error", "message": str(e)})
            
    return {"results": results}

@mcp.tool()
def vcs_diff(target: str) -> dict:
    """Show unified diff between TARGET snapshot and current disk file."""
    try:
        blob_hash = _resolve_target(target)
        filepath = resolve_path(blob_hash)
        if filepath is None: return {"status": "error", "message": f"blob hash '{blob_hash}' not found"}
        base = load_snapshot(blob_hash)
        if base is None: return {"status": "error", "message": f"no snapshot found"}
        if not os.path.exists(filepath): return {"status": "error", "message": f"file not found"}
        with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
            current = fh.read()
        base_lines = base.splitlines(keepends=True)
        current_lines = current.splitlines(keepends=True)
        diff_text = "".join(difflib.unified_diff(
            [f"{i+1}: {line}" for i, line in enumerate(base_lines)],
            [f"{i+1}: {line}" for i, line in enumerate(current_lines)],
            fromfile=f"blob:{blob_hash[:8]}", tofile=str(filepath)
        ))
        return _format_result({"status": "ok", "blob": blob_hash, "path": filepath, "diff": diff_text, "changed": bool(diff_text)})
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def vcs_skeleton(filepath: str, start: Optional[int] = None, end: Optional[int] = None) -> dict:
    """Generate file skeleton (collapsed functions)."""
    try:
        script_path = os.path.join(os.path.dirname(__file__), "skeleton.py")
        cmd = [sys.executable, script_path, filepath, "--json"]
        if start is not None: cmd.extend(["--start", str(start)])
        if end is not None: cmd.extend(["--end", str(end)])
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
        import json
        skel_data = json.loads(out)
        blob_hash = _resolve_target(filepath)
        return _format_result({"status": "ok", "blob": blob_hash, "content": skel_data["output"]})
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def vcs_tree(path: str = ".", depth: int = 2, show_all: bool = False) -> dict:
    """Tree-style directory view, respects .gitignore."""
    try:
        script_path = os.path.join(os.path.dirname(__file__), "tree.py")
        cmd = [sys.executable, script_path, path, "--depth", str(depth)]
        if show_all: cmd.append("--all")
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
        return {"status": "ok", "output": out}
    except subprocess.CalledProcessError as e:
        return {"status": "error", "output": e.output}

@mcp.tool()
def vcs_grep(query: str, path: str = ".", ignore_case: bool = False, regex: bool = False) -> dict:
    """Grep with function/class context. Outputs blob hashes."""
    try:
        script_path = os.path.join(os.path.dirname(__file__), "grep.py")
        cmd = [sys.executable, script_path, query, path]
        if ignore_case: cmd.append("-i")
        if regex: cmd.append("-r")
        try:
            out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
            if not out.strip(): raise subprocess.CalledProcessError(1, cmd, output="")
            return {"status": "ok", "output": out}
        except subprocess.CalledProcessError:
            fallback_cmd = ["grep", "-rn"]
            if ignore_case: fallback_cmd.append("-i")
            if regex: fallback_cmd.append("-E")
            fallback_cmd.extend([query, path])
            try:
                out = subprocess.check_output(fallback_cmd, text=True, stderr=subprocess.STDOUT)
                return {"status": "ok", "output": f"--- No context results. Standard grep: ---\n{out}"}
            except subprocess.CalledProcessError:
                return {"status": "ok", "output": "No results found."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def vcs_fmt(path: str = ".", check_only: bool = False) -> dict:
    """Format staged files natively."""
    try:
        script_path = os.path.join(os.path.dirname(__file__), "fmt.sh")
        cmd = ["bash", script_path]
        if check_only: cmd.append("--check")
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT, cwd=path)
        return {"status": "ok", "output": out}
    except subprocess.CalledProcessError as e:
        return {"status": "error", "output": e.output}

@mcp.tool()
def vcs_test(command: str, path: str = ".") -> dict:
    """Run tests & show ONLY failures/summary."""
    try:
        import shlex
        script_path = os.path.join(os.path.dirname(__file__), "test.sh")
        cmd = ["bash", script_path] + shlex.split(command)
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT, cwd=path)
        return {"status": "ok", "output": out}
    except subprocess.CalledProcessError as e:
        return {"status": "error", "output": e.output}

@mcp.tool()
def vcs_status() -> dict:
    """List all blob -> filepath mappings."""
    try:
        repo_root = _find_repo_root()
        data = _load_store(repo_root)
        return {"repo_root": repo_root, "blobs": data["blobs"]}
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    mcp.run()
