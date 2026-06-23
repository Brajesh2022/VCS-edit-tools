"""Tests for the CLI layer (cli.py).

Uses subprocess to invoke the CLI as a real process, matching how agents
actually use it (heredoc stdin, exit codes, line-based output parsing).
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLI = str(PROJECT_ROOT / "cli.py")


def _run(args, *, input_data=None, cwd=None):
    """Run the CLI and return (exit_code, stdout, stderr)."""
    cmd = [sys.executable, CLI] + args
    result = subprocess.run(
        cmd,
        input=input_data,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=30,
    )
    return result.returncode, result.stdout, result.stderr


def _parse_blob(stdout):
    """Extract the blob hash from the first 'blob: XXXX' line."""
    for line in stdout.splitlines():
        if line.startswith("blob:"):
            val = line.split(":", 1)[1].strip()
            return val.split()[0]
    return None


def _parse_status_field(stdout, field):
    """Extract a field value from 'field: value' output."""
    for line in stdout.splitlines():
        if line.startswith(f"{field}:"):
            return line.split(":", 1)[1].strip()
    return None


# ---------------------------------------------------------------------------
# vcs read
# ---------------------------------------------------------------------------

def test_read_success(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("hello\nworld\n")
    code, out, err = _run(["read", str(f)])
    assert code == 0
    assert "blob:" in out
    assert "Code Lines: 1 to 2" in out
    assert "1: hello" in out
    assert "2: world" in out


def test_read_with_range(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("l1\nl2\nl3\nl4\nl5\n")
    code, out, err = _run(["read", str(f), "2-4"])
    assert code == 0
    assert "Code Lines: 2 to 4" in out
    assert "2: l2" in out
    assert "3: l3" in out
    assert "4: l4" in out
    assert "1: l1" not in out


def test_read_missing_file_exit_2(tmp_repo):
    code, out, err = _run(["read", str(tmp_repo / "nope.txt")])
    assert code == 2
    assert "error" in err.lower() or "not found" in err.lower()


def test_read_truncation(tmp_repo):
    f = tmp_repo / "big.txt"
    f.write_text("".join(f"line {i}\n" for i in range(1, 1001)))
    code, out, err = _run(["read", str(f)])
    assert code == 0
    assert "Code Lines: 1 to 1000 (skeleton)" in out
    assert "truncated:" not in out


# ---------------------------------------------------------------------------
# vcs replace
# ---------------------------------------------------------------------------

def test_replace_happy_path(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("a\nb\nc\n")
    code, out, _ = _run(["read", str(f)])
    blob = _parse_blob(out)
    assert blob is not None

    code, out, err = _run(["replace", blob, "2-2"], input_data="B!\n")
    assert code == 0
    assert "status: ok" in out
    assert f.read_text() == "a\nB!\nc\n"


def test_replace_conflict_exit_1(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("a\nb\nc\nd\ne\n")
    code, out, _ = _run(["read", str(f)])
    blob = _parse_blob(out)
    # Modify line 3 (inside agent's range of 3-4)
    f.write_text("a\nb\nCC!\nd\ne\n")
    code, out, err = _run(["replace", blob, "3-4"], input_data="X\nY\n")
    assert code == 1
    assert "CONFLICT" in out


def test_replace_auto_merge_exit_0(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("a\nb\nc\nd\ne\nf\n")
    code, out, _ = _run(["read", str(f)])
    blob = _parse_blob(out)
    # Modify line 1 (outside agent's range of 4-5)
    f.write_text("A!\nb\nc\nd\ne\nf\n")
    code, out, err = _run(["replace", blob, "4-5"], input_data="DD\nEE\n")
    assert code == 0
    assert "auto_merged" in out


def test_replace_unknown_blob_exit_2(tmp_repo):
    code, out, err = _run(["replace", "nonexistent_hash", "1-1"], input_data="X\n")
    assert code == 2


def test_replace_filepath_as_target(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("a\nb\nc\n")
    code, out, err = _run(["replace", str(f), "2-2"], input_data="B!\n")
    assert code == 0
    assert "status: ok" in out
    assert f.read_text() == "a\nB!\nc\n"


# ---------------------------------------------------------------------------
# vcs insert
# ---------------------------------------------------------------------------

def test_insert(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("a\nc\n")
    code, out, err = _run(["insert", str(f), "2"], input_data="b1\nb2\n")
    assert code == 0
    assert "status: ok" in out
    assert f.read_text() == "a\nb1\nb2\nc\n"


def test_insert_invalid_line_exit_2(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("a\nb\n")
    code, out, err = _run(["insert", str(f), "abc"], input_data="X\n")
    assert code == 2


def test_insert_line_zero_exit_2(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("a\nb\n")
    code, out, err = _run(["insert", str(f), "0"], input_data="X\n")
    assert code == 2


# ---------------------------------------------------------------------------
# vcs delete
# ---------------------------------------------------------------------------

def test_delete(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("a\nb\nc\nd\n")
    code, out, err = _run(["delete", str(f), "2-3"])
    assert code == 0
    assert "status: ok" in out
    assert f.read_text() == "a\nd\n"


# ---------------------------------------------------------------------------
# vcs batch
# ---------------------------------------------------------------------------

def test_batch(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("A\nB\nC\nD\nE\n")
    edits = json.dumps([
        {"target": str(f), "type": "replace", "line_range": "2-2", "content": "BB"},
    ])
    code, out, err = _run(["batch"], input_data=edits)
    assert code == 0
    assert "1 ok" in out
    assert "BB\n" in f.read_text()


def test_batch_invalid_json_exit_2(tmp_repo):
    code, out, err = _run(["batch"], input_data="not json")
    assert code == 2


def test_batch_error_handling(tmp_repo):
    edits = json.dumps([
        {"target": "nonexistent.txt", "type": "replace", "line_range": "1-1", "content": "X"},
    ])
    code, out, err = _run(["batch"], input_data=edits)
    assert code == 2
    assert "1 error" in out


# ---------------------------------------------------------------------------
# vcs diff
# ---------------------------------------------------------------------------

def test_diff_no_changes(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("hello\n")
    _run(["read", str(f)])  # snapshot it
    code, out, err = _run(["diff", str(f)])
    assert code == 0
    assert "no changes" in out


def test_diff_with_changes(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("hello\nworld\n")
    code, out, _ = _run(["read", str(f)])
    blob = _parse_blob(out)
    f.write_text("hello\nWORLD\n")
    code, out, err = _run(["diff", blob])
    assert code == 0
    assert "---" in out
    assert "+++" in out


# ---------------------------------------------------------------------------
# vcs skeleton
# ---------------------------------------------------------------------------

def test_skeleton(tmp_repo):
    f = tmp_repo / "test.py"
    f.write_text("def foo():\n    pass\n\ndef bar():\n    pass\n")
    code, out, err = _run(["skeleton", str(f)])
    assert code == 0
    assert "blob:" in out
    assert "def foo" in out
    assert "def bar" in out


# ---------------------------------------------------------------------------
# vcs tree
# ---------------------------------------------------------------------------

def test_tree(tmp_repo):
    (tmp_repo / "subdir").mkdir()
    (tmp_repo / "subdir" / "file.txt").write_text("hi\n")
    code, out, err = _run(["tree", str(tmp_repo), "--depth", "1"])
    assert code == 0
    assert "subdir" in out


# ---------------------------------------------------------------------------
# vcs status
# ---------------------------------------------------------------------------

def test_status(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("a\n")
    _run(["read", str(f)])
    code, out, err = _run(["status"])
    assert code == 0
    # Should mention the file or blob
    assert "a.txt" in out or "blob" in out


# ---------------------------------------------------------------------------
# vcs help / version
# ---------------------------------------------------------------------------

def test_version():
    code, out, err = _run(["--version"])
    assert code == 0
    assert "1.0.0" in out


def test_help_lists_commands():
    code, out, err = _run(["--help"])
    assert code == 0
    for cmd in ("read", "replace", "insert", "delete", "batch", "diff",
                "skeleton", "tree", "grep", "fmt", "test", "status"):
        assert cmd in out


def test_unknown_command_exit_2():
    code, out, err = _run(["foobar"])
    assert code == 2
    assert "unknown" in err.lower()


# ---------------------------------------------------------------------------
# vcs short blob prefix
# ---------------------------------------------------------------------------

def test_short_blob_prefix_in_replace(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("a\nb\nc\n")
    code, out, _ = _run(["read", str(f)])
    blob = _parse_blob(out)
    short = blob[:8]
    code, out, err = _run(["replace", short, "2-2"], input_data="B!\n")
    assert code == 0
    assert "ok" in out


def test_invalid_line_range_exit_2(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("a\nb\n")
    code, out, err = _run(["read", str(f), "not-a-range"])
    assert code == 2


# ---------------------------------------------------------------------------
# Dollar-sign safety (heredoc with single-quoted EOF)
# ---------------------------------------------------------------------------

def test_dollar_sign_safety(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("a\nb\nc\n")
    dollar_content = "line_with_$DOLLAR and ${CURLY} and $(sub)\n"
    code, out, err = _run(["replace", str(f), "2-2"], input_data=dollar_content)
    assert code == 0
    assert "$DOLLAR" in f.read_text()
    assert "${CURLY}" in f.read_text()
    assert "$(sub)" in f.read_text()
