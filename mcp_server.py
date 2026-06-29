#!/usr/bin/env python3
import os
import sys
import tempfile
from typing import Optional, List, Literal, Union
from typing_extensions import Annotated
from pydantic import BaseModel, Field

# Add the directory containing core to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP
from core.replace import replace as do_replace
from core.blob import get_blob_hash
from core.store import register, save_snapshot

mcp = FastMCP("vcs-edit")

def _resolve_target(target: str, cwd: str = None) -> str:
    """If target is an existing filepath, snapshot it and return its current blob hash.
    Otherwise assume it's a blob hash and return it.
    """
    target_path = os.path.join(cwd, target) if cwd and not os.path.isabs(target) else target
    if os.path.exists(target_path):
        blob = get_blob_hash(target_path)
        register(blob, target_path)
        with open(target_path, "r", encoding="utf-8", errors="replace", newline="") as fh:
            save_snapshot(blob, fh.read())
        return blob
    return target

def _format_result(payload: dict) -> dict:
    if payload.get("status") == "error":
        return {"status": f"error - {payload.get('message', 'unknown error')}"}
    return {"status": payload.get("status", "ok")}
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
    range: str
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
    blob: Optional[str] = None
    range: Optional[str] = None
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
def vcs_edit(edits: list[EditOperation], cwd: Optional[str] = None) -> dict:
    """Apply multiple edits (replace/insert/delete/create) efficiently."""
    results = []
    
    if cwd:
        for edit in edits:
            if not os.path.isabs(edit.filepath):
                edit.filepath = os.path.join(cwd, edit.filepath)
    
    for i, edit in enumerate(edits):
        try:
            if edit.action == "create":
                if os.path.exists(edit.filepath):
                    results.append({"status": "error - file already exists"})
                    continue
                parent = os.path.dirname(os.path.abspath(edit.filepath))
                if parent:
                    os.makedirs(parent, exist_ok=True)
                content = edit.content
                if content and not content.endswith('\n'):
                    content += '\n'
                with open(edit.filepath, "w", encoding="utf-8") as f:
                    f.write(content)
                results.append({"status": "ok"})
                continue
                
            if edit.action == "delete" and not edit.range:
                if os.path.exists(edit.filepath):
                    os.remove(edit.filepath)
                    results.append({"status": "ok"})
                else:
                    results.append({"status": "error - file not found"})
                continue
            
            # Resolve blob for replace/insert/delete(partial)
            try:
                blob_hash = _resolve_target(edit.filepath, cwd) if not edit.blob else _resolve_target(edit.blob, cwd)
            except Exception as e:
                results.append({"status": f"error - {str(e)}"})
                continue
                
            search_root = os.path.dirname(os.path.abspath(edit.filepath))
            if not search_root: search_root = "."
            
            if edit.action == "replace":
                tmp_path = _write_temp(edit.content, dir=search_root)
                try:
                    res = do_replace(blob_hash, edit.range, tmp_path, search_root=search_root)
                    results.append(_format_result(res))
                finally:
                    if os.path.exists(tmp_path): os.remove(tmp_path)
            
            elif edit.action == "insert":
                tmp_path = _write_temp(edit.content, dir=search_root)
                try:
                    res = do_replace(blob_hash, f"{edit.line}-{edit.line-1}", tmp_path, search_root=search_root)
                    results.append(_format_result(res))
                finally:
                    if os.path.exists(tmp_path): os.remove(tmp_path)
                    
            elif edit.action == "delete":
                res = do_replace(blob_hash, edit.range, os.devnull, search_root=search_root)
                results.append(_format_result(res))
                
        except Exception as e:
            results.append({"status": f"error - {str(e)}"})
            
    return {"results": results}

if __name__ == "__main__":
    mcp.run()
