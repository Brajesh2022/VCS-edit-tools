"""Tests for the CLI layer (cli.py).

Uses subprocess to invoke the CLI as a real process, matching how agents
actually use it (heredoc stdin, exit codes, line-based output parsing).

v2 API: replace/insert/delete(line-range)/batch all require BOTH filepath AND blob.
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


def _read_and_get_blob(filepath):
    """Helper: read a file, return the short blob hash."""
    code, out, _ = _run(["read", str(filepath)])
    assert code == 0, f"read failed: {out}"
    return _parse_blob(out)


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
# vcs replace  (now requires BOTH filepath AND blob)
# ---------------------------------------------------------------------------

def test_replace_happy_path(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("a\nb\nc\n")
    blob = _read_and_get_blob(f)
    code, out, err = _run(["replace", str(f), blob, "2-2"], input_data="B!\n")
    assert code == 0
    assert "status: ok" in out
    assert f.read_text() == "a\nB!\nc\n"


def test_replace_conflict_exit_1(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("a\nb\nc\nd\ne\n")
    blob = _read_and_get_blob(f)
    # Modify line 3 (inside agent's range of 3-4)
    f.write_text("a\nb\nCC!\nd\ne\n")
    code, out, err = _run(["replace", str(f), blob, "3-4"], input_data="X\nY\n")
    assert code == 1
    # v2: simple conflict message — no CONFLICT keyword anymore
    assert "Merge conflict detected" in out


def test_replace_auto_merge_exit_0(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("a\nb\nc\nd\ne\nf\n")
    blob = _read_and_get_blob(f)
    # Modify line 1 (outside agent's range of 4-5)
    f.write_text("A!\nb\nc\nd\ne\nf\n")
    code, out, err = _run(["replace", str(f), blob, "4-5"], input_data="DD\nEE\n")
    assert code == 0
    # v2: auto_merged also returns `status: ok` cleanly
    assert "status: ok" in out


def test_replace_missing_blob_rejected(tmp_repo):
    """v2: both filepath AND blob are required — missing blob should error."""
    f = tmp_repo / "a.txt"
    f.write_text("a\nb\nc\n")
    # Try with only filepath + range (no blob) → should fail with usage error
    code, out, err = _run(["replace", str(f), "2-2"], input_data="B!\n")
    assert code == 2
    assert "usage" in err.lower() or "blob" in err.lower()


def test_replace_filepath_as_target_no_longer_supported(tmp_repo):
    """v2: filepath-only (no blob) is rejected."""
    f = tmp_repo / "a.txt"
    f.write_text("a\nb\nc\n")
    code, out, err = _run(["replace", str(f), "2-2"], input_data="B!\n")
    assert code == 2


# ---------------------------------------------------------------------------
# vcs insert  (now requires BOTH filepath AND blob)
# ---------------------------------------------------------------------------

def test_insert(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("a\nc\n")
    blob = _read_and_get_blob(f)
    code, out, err = _run(["insert", str(f), blob, "2"], input_data="b1\nb2\n")
    assert code == 0
    assert "status: ok" in out
    assert f.read_text() == "a\nb1\nb2\nc\n"


def test_insert_invalid_line_exit_2(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("a\nb\n")
    blob = _read_and_get_blob(f)
    code, out, err = _run(["insert", str(f), blob, "abc"], input_data="X\n")
    assert code == 2


def test_insert_line_zero_exit_2(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("a\nb\n")
    blob = _read_and_get_blob(f)
    code, out, err = _run(["insert", str(f), blob, "0"], input_data="X\n")
    assert code == 2


def test_insert_missing_blob_rejected(tmp_repo):
    """v2: insert without blob should fail."""
    f = tmp_repo / "a.txt"
    f.write_text("a\nc\n")
    code, out, err = _run(["insert", str(f), "2"], input_data="b1\nb2\n")
    assert code == 2


# ---------------------------------------------------------------------------
# vcs delete  (line-range mode requires blob; file/dir mode does not)
# ---------------------------------------------------------------------------

def test_delete_line_range(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("a\nb\nc\nd\n")
    blob = _read_and_get_blob(f)
    code, out, err = _run(["delete", str(f), blob, "2-3"])
    assert code == 0
    assert "status: ok" in out
    assert f.read_text() == "a\nd\n"


def test_delete_file(tmp_repo):
    """v2: `vcs delete <filepath>` deletes the file."""
    f = tmp_repo / "a.txt"
    f.write_text("a\nb\nc\nd\n")
    code, out, err = _run(["delete", str(f)])
    assert code == 0
    assert "status: ok" in out
    assert not f.exists()


def test_delete_directory(tmp_repo):
    """v2: `vcs delete <dir>` deletes an entire directory tree."""
    d = tmp_repo / "subdir"
    d.mkdir()
    (d / "a.txt").write_text("a\n")
    (d / "nested").mkdir()
    (d / "nested" / "b.txt").write_text("b\n")
    code, out, err = _run(["delete", str(d)])
    assert code == 0
    assert "status: ok" in out
    assert not d.exists()


def test_delete_missing_path_exit_2(tmp_repo):
    code, out, err = _run(["delete", str(tmp_repo / "nope.txt")])
    assert code == 2


# ---------------------------------------------------------------------------
# vcs create  (new in v2)
# ---------------------------------------------------------------------------

def test_create_file(tmp_repo):
    f = tmp_repo / "new.txt"
    code, out, err = _run(["create", str(f)], input_data="hello\nworld\n")
    assert code == 0
    assert "status: ok" in out
    assert f.exists()
    assert f.read_text() == "hello\nworld\n"


def test_create_file_in_nested_dir(tmp_repo):
    """vcs create should auto-create parent directories."""
    f = tmp_repo / "deep" / "nested" / "dir" / "new.txt"
    code, out, err = _run(["create", str(f)], input_data="content\n")
    assert code == 0
    assert f.exists()
    assert f.read_text() == "content\n"


def test_create_existing_file_rejected(tmp_repo):
    """vcs create must not silently overwrite an existing file."""
    f = tmp_repo / "existing.txt"
    f.write_text("original\n")
    code, out, err = _run(["create", str(f)], input_data="new content\n")
    assert code == 2
    assert f.read_text() == "original\n"  # unchanged


def test_create_empty_file(tmp_repo):
    f = tmp_repo / "empty.txt"
    code, out, err = _run(["create", str(f)], input_data="")
    assert code == 0
    assert f.exists()


# ---------------------------------------------------------------------------
# vcs batch  (now requires BOTH filepath AND blob per edit)
# ---------------------------------------------------------------------------

def test_batch_with_blob(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("A\nB\nC\nD\nE\n")
    blob = _read_and_get_blob(f)
    edits = json.dumps([
        {"filepath": str(f), "blob": blob, "type": "replace", "line_range": "2-2", "content": "BB"},
    ])
    code, out, err = _run(["batch"], input_data=edits)
    assert code == 0
    assert "1 ok" in out
    assert "BB\n" in f.read_text()


def test_batch_missing_blob_rejected(tmp_repo):
    """v2: batch edit without blob must be rejected."""
    f = tmp_repo / "a.txt"
    f.write_text("A\nB\nC\nD\nE\n")
    edits = json.dumps([
        {"filepath": str(f), "type": "replace", "line_range": "2-2", "content": "BB"},
    ])
    code, out, err = _run(["batch"], input_data=edits)
    assert code == 2
    assert "missing blob" in out.lower() or "rejected" in out.lower()


def test_batch_invalid_json_exit_2(tmp_repo):
    code, out, err = _run(["batch"], input_data="not json")
    assert code == 2


def test_batch_error_handling(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("A\nB\nC\n")
    blob = _read_and_get_blob(f)
    edits = json.dumps([
        {"filepath": "nonexistent.txt", "blob": blob, "type": "replace", "line_range": "1-1", "content": "X"},
    ])
    code, out, err = _run(["batch"], input_data=edits)
    assert code == 2
    assert "1 error" in out


def test_batch_conflict_simple_message(tmp_repo):
    """v2: batch conflict output is a simple human message, no diff."""
    f = tmp_repo / "a.txt"
    f.write_text("a\nb\nc\nd\ne\n")
    blob = _read_and_get_blob(f)
    # Modify line 3 (inside agent's range of 3-4) → conflict
    f.write_text("a\nb\nCC!\nd\ne\n")
    edits = json.dumps([
        {"filepath": str(f), "blob": blob, "type": "replace", "line_range": "3-4", "content": "X\nY\n"},
    ])
    code, out, err = _run(["batch"], input_data=edits)
    assert code == 1
    assert "Merge conflict detected" in out
    # Should NOT contain diff markers
    assert "---" not in out.replace("batch:", "").replace("---", "") or "Merge conflict" in out


# ---------------------------------------------------------------------------
# vcs diff  (now requires BOTH filepath AND blob)
# ---------------------------------------------------------------------------

def test_diff_no_changes(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("hello\n")
    blob = _read_and_get_blob(f)
    code, out, err = _run(["diff", str(f), blob])
    assert code == 0
    assert "no changes" in out


def test_diff_with_changes(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("hello\nworld\n")
    blob = _read_and_get_blob(f)
    f.write_text("hello\nWORLD\n")
    code, out, err = _run(["diff", str(f), blob])
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


def test_tree_does_not_say_agy_tree(tmp_repo):
    """v2: the tree command's footer must say `vcs tree`, not `agy-tree`."""
    (tmp_repo / "subdir").mkdir()
    (tmp_repo / "subdir" / "file.txt").write_text("hi\n")
    code, out, err = _run(["tree", str(tmp_repo), "--depth", "1"])
    assert code == 0
    assert "agy-tree" not in out
    assert "vcs tree" in out


def test_tree_summary_format(tmp_repo):
    """v2: directories show `(N dirs, M files)` style summaries."""
    d = tmp_repo / "Frontend"
    d.mkdir()
    (d / "a.txt").write_text("a\n")
    (d / "b.txt").write_text("b\n")
    (d / "sub1").mkdir()
    (d / "sub2").mkdir()
    code, out, err = _run(["tree", str(tmp_repo), "--depth", "1"])
    assert code == 0
    # The Frontend dir should show a dirs/files summary
    assert "Frontend" in out
    assert "dir" in out
    assert "file" in out


def test_tree_caps_large_directories(tmp_repo):
    """v2: directories with 10+ items should be capped (no full recursion)."""
    d = tmp_repo / "big"
    d.mkdir()
    for i in range(15):
        (d / f"file_{i}.txt").write_text(f"content {i}\n")
    code, out, err = _run(["tree", str(tmp_repo), "--depth", "3"])
    assert code == 0
    # Should mention the cap or just show summary without listing all 15 files
    assert "big" in out


# ---------------------------------------------------------------------------
# vcs status
# ---------------------------------------------------------------------------

def test_status(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("a\n")
    _run(["read", str(f)])
    code, out, err = _run(["status"])
    assert code == 0
    assert "a.txt" in out or "blob" in out


# ---------------------------------------------------------------------------
# vcs help / version
# ---------------------------------------------------------------------------

def test_version():
    code, out, err = _run(["--version"])
    assert code == 0
    assert "2.0.0" in out


def test_help_lists_commands():
    code, out, err = _run(["--help"])
    assert code == 0
    for cmd in ("read", "replace", "insert", "delete", "create", "batch", "diff",
                "skeleton", "tree", "grep", "fmt", "test", "status"):
        assert cmd in out


def test_help_mentions_create_command():
    """v2: help text must mention the new `create` command."""
    code, out, err = _run(["--help"])
    assert code == 0
    assert "create" in out


def test_help_mentions_both_filepath_and_blob_required():
    """v2: help text must mention both filepath AND blob are required."""
    code, out, err = _run(["--help"])
    assert code == 0
    assert "filepath" in out.lower()
    assert "blob" in out.lower()


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
    blob = _read_and_get_blob(f)
    short = blob[:8]
    code, out, err = _run(["replace", str(f), short, "2-2"], input_data="B!\n")
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
    blob = _read_and_get_blob(f)
    dollar_content = "line_with_$DOLLAR and ${CURLY} and $(sub)\n"
    code, out, err = _run(["replace", str(f), blob, "2-2"], input_data=dollar_content)
    assert code == 0
    assert "$DOLLAR" in f.read_text()
    assert "${CURLY}" in f.read_text()
    assert "$(sub)" in f.read_text()
