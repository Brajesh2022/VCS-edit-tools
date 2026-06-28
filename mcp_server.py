#!/usr/bin/env python3
"""
VCS Edit Tool - MCP Server

This MCP server provides a single tool `vcs_batch_edit` that allows
AI agents to perform batch edits (replace, insert, delete, create)
using the VCS blob-hash concurrency control.
"""
import os
import sys
import json
import tempfile
import subprocess
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field
from typing import Literal, Optional, List

# Initialize FastMCP Server
mcp = FastMCP("vcs-edit")

from typing_extensions import Annotated
from typing import Literal, Optional, List, Union
from pydantic import BaseModel, Field

class ReplaceOperation(BaseModel):
    action: Literal["replace"]
    filepath: str
    blob: str = Field(description="Blob hash of the file read, prevents conflicts.")
    start_line: int = Field(description="Start line (1-indexed).")
    end_line: int = Field(description="End line (1-indexed).")
    content: str = Field(description="New content for replace.")

class InsertOperation(BaseModel):
    action: Literal["insert"]
    filepath: str
    blob: str = Field(description="Blob hash of the file read, prevents conflicts.")
    line: int = Field(description="Line number for insert.")
    content: str = Field(description="New content to insert.")

class DeleteOperation(BaseModel):
    action: Literal["delete"]
    filepath: str
    blob: str = Field(description="Blob hash of the file read, prevents conflicts.")
    start_line: int = Field(description="Start line (1-indexed).")
    end_line: int = Field(description="End line (1-indexed).")

class CreateOperation(BaseModel):
    action: Literal["create"]
    filepath: str
    content: str = Field(description="Content of the new file.")

EditOperation = Annotated[
    Union[ReplaceOperation, InsertOperation, DeleteOperation, CreateOperation], 
    Field(discriminator="action")
]
@mcp.tool()
def vcs_batch_edit(edits: List[EditOperation]) -> str:
    """
    Perform atomic batch edits using VCS blob-hash concurrency control.
    Supports replace, insert, delete, and create operations.
    """
    # We will translate this into a format `vcs batch` understands
    # Since `vcs batch` natively accepts JSON, we can just shell out to the CLI
    # which ensures all the same rules, logging, and error handling apply!
    cli_edits = []
    results = []
    cli_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cli.py")
    
    for edit in edits:
        if edit.action == "create":
            if edit.content is None:
                results.append(f"Error: create requires content for {edit.filepath}")
                continue
            
            # Execute create independently
            try:
                res = subprocess.run(
                    [sys.executable, cli_script, "create", edit.filepath],
                    input=edit.content,
                    capture_output=True,
                    text=True
                )
                output = res.stdout.strip()
                if res.stderr.strip(): output += "\n" + res.stderr.strip()
                if res.returncode != 0:
                    results.append(f"Create Failed ({edit.filepath}):\n{output}")
                else:
                    results.append(f"Create OK ({edit.filepath})")
            except Exception as e:
                results.append(f"Create Error ({edit.filepath}): {e}")
            continue

        # Prepare batch edits for replace, insert, delete
        op = {
            "type": edit.action,
            "filepath": edit.filepath,
        }
        blob = getattr(edit, "blob", None)
        if blob:
            op["blob"] = blob
            
        if edit.action in ("replace", "delete"):
            start_line = getattr(edit, "start_line", None)
            end_line = getattr(edit, "end_line", None)
            if start_line is not None and end_line is not None:
                op["line_range"] = f"{start_line}-{end_line}"
            else:
                return f"Error: replace/delete require start_line and end_line for {edit.filepath}"
        elif edit.action == "insert":
            line = getattr(edit, "line", None)
            if line is not None:
                op["line_str"] = str(line)
            else:
                return f"Error: insert requires line for {edit.filepath}"
                return f"Error: insert requires line for {edit.filepath}"
                
        content = getattr(edit, "content", None)
        if content is not None:
            op["content"] = content
        cli_edits.append(op)
        
    if cli_edits:
        try:
            result = subprocess.run(
                [sys.executable, cli_script, "batch"],
                input=json.dumps(cli_edits),
                capture_output=True,
                text=True
            )
            
            output = result.stdout.strip()
            if result.stderr.strip():
                output += "\n" + result.stderr.strip()
                
            if result.returncode != 0:
                results.append(f"Batch Failed (Exit {result.returncode}):\n{output}")
            else:
                results.append(output if output else "Batch OK")
        except Exception as e:
            results.append(f"Batch Error: {e}")
            
    return "\n---\n".join(results)

if __name__ == "__main__":
    mcp.run()
