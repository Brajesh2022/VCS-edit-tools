import pytest
import tempfile
import os
from core.conflict import resolve

def _write(content):
    fd, path = tempfile.mkstemp()
    with open(path, "w") as f:
        f.write(content)
    return path

def test_resolve_no_conflict():
    base = _write("line 1\nline 2\nline 3\n")
    ours = _write("line 1\nline 2 modified\nline 3\n")
    theirs = _write("line 1\nline 2\nline 3 appended\n")
    
    # line_range for ours was "2"
    result = resolve(base, ours, theirs, "2-2")
    assert result["status"] == "auto_merged"
    
    merged = result["new_content"]
    assert "line 2 modified" in merged
    assert "line 3 appended" in merged
    
    os.remove(base)
    os.remove(ours)
    os.remove(theirs)

def test_resolve_conflict():
    base = _write("line 1\nline 2\nline 3\n")
    ours = _write("line 1\nline 2 modified by current\nline 3\n")
    theirs = _write("line 1\nline 2 modified by theirs\nline 3\n")
    
    result = resolve(base, ours, theirs, "2-2")
    assert result["status"] == "conflict"
    
    os.remove(base)
    os.remove(ours)
    os.remove(theirs)
