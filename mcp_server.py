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
def vcs_edit(edits: list[EditOperation]) -> dict:
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

if __name__ == "__main__":
    mcp.run()
