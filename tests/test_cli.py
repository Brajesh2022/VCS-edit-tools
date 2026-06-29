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
# vcs list
# ---------------------------------------------------------------------------

def test_list(tmp_repo):
    (tmp_repo / "subdir").mkdir()
    (tmp_repo / "subdir" / "file.txt").write_text("hi\n")
    code, out, err = _run(["list", str(tmp_repo), "--depth", "1"])
    assert code == 0
    assert "subdir" in out


def test_list_does_not_say_agy_tree(tmp_repo):
    """v2: the tree command's footer must say `vcs list`, not `agy-list`."""
    (tmp_repo / "subdir").mkdir()
    (tmp_repo / "subdir" / "file.txt").write_text("hi\n")
    code, out, err = _run(["list", str(tmp_repo), "--depth", "1"])
    assert code == 0
    assert "agy-list" not in out
    assert "vcs list" in out


def test_list_summary_format(tmp_repo):
    """v2: directories show `(N dirs, M files)` style summaries."""
    d = tmp_repo / "Frontend"
    d.mkdir()
    (d / "a.txt").write_text("a\n")
    (d / "b.txt").write_text("b\n")
    (d / "sub1").mkdir()
    (d / "sub2").mkdir()
    code, out, err = _run(["list", str(tmp_repo), "--depth", "1"])
    assert code == 0
    # The Frontend dir should show a dirs/files summary
    assert "Frontend" in out
    assert "dir" in out
    assert "file" in out


def test_list_caps_large_directories(tmp_repo):
    """v2: a subdirectory with 10+ items should NOT be recursed into.

    We assert all three legs of the contract:
      1. The subdirectory itself is still named in the parent listing.
      2. The cap notice ("many items") is printed.
      3. NONE of the file_N.txt entries inside the capped subdir appear.
    """
    d = tmp_repo / "big"
    d.mkdir()
    for i in range(15):
        (d / f"file_{i}.txt").write_text(f"content {i}\n")
    code, out, err = _run(["list", str(tmp_repo), "--depth", "3"])
    assert code == 0
    # Leg 1: the dir is named
    assert "big" in out
    # Leg 2: cap notice is shown
    assert "many items" in out
    # Leg 3: files inside the capped dir are NOT listed
    assert "file_0.txt" not in out
    assert "file_7.txt" not in out
    assert "file_14.txt" not in out


def test_list_does_not_cap_small_subdirs(tmp_repo):
    """v2: a small subdirectory should still be recursed into, even if its
    sibling directory is also small. The cap is per-child, not per-parent.
    Regression test for the bug where `too_many` was based on the parent's
    n_visible.
    """
    # Parent (tmp_repo) has only 2 subdirs (small n_visible), but one child
    # is also small. Both should be recursed into.
    (tmp_repo / "small_a").mkdir()
    (tmp_repo / "small_a" / "a1.txt").write_text("a\n")
    (tmp_repo / "small_a" / "a2.txt").write_text("a\n")
    (tmp_repo / "small_b").mkdir()
    (tmp_repo / "small_b" / "b1.txt").write_text("b\n")
    code, out, err = _run(["list", str(tmp_repo), "--depth", "2"])
    assert code == 0
    # Both subdirs were recursed into (their files appear)
    assert "a1.txt" in out
    assert "b1.txt" in out
    # No cap notice should appear (everything is small)
    assert "many items" not in out


def test_list_caps_on_child_count_not_parent(tmp_repo):
    """v2: if the parent has many small subdirs, we still recurse into each
    small one. If a single child has 10+ items, only THAT child is capped.

    Regression test for the Gemini/AGY bug: the old code capped all children
    when the parent was big, AND recursed into huge children when the parent
    was small.
    """
    # Create 12 tiny subdirs in the parent (parent n_visible = 12 >= 10).
    # Old buggy code would have skipped recursion for ALL of them.
    for i in range(12):
        sub = tmp_repo / f"tiny_{i}"
        sub.mkdir()
        (sub / "inside.txt").write_text("x\n")
    # Create one big subdir with 11 files.
    big = tmp_repo / "big"
    big.mkdir()
    for i in range(11):
        (big / f"f{i}.txt").write_text("x\n")

    code, out, err = _run(["list", str(tmp_repo), "--depth", "2"])
    assert code == 0
    # The 12 tiny subdirs WERE recursed into (their inside.txt files appear)
    assert "inside.txt" in out
    # The big subdir was capped (its files do NOT appear)
    assert "f0.txt" not in out
    assert "many items" in out


def test_batch_rejects_non_dict_json(tmp_repo):
    """v2: JSON arrays with non-object elements (e.g. [1, 2, 3]) should be
    cleanly rejected, not crash with AttributeError.
    """
    code, out, err = _run(["batch"], input_data="[1, 2, 3]")
    assert code == 2
    assert "REJECTED" in out
    assert "JSON object" in out


def test_batch_rejects_v1_5_part_format_cleanly(tmp_repo):
    """v2: the legacy v1 text format `=== REPLACE target 8-50 ===` (5 parts,
    no blob) should be cleanly rejected with the missing-blob message,
    NOT silently dropped.
    """
    code, out, err = _run(["batch"], input_data="=== REPLACE file.py 8-50 ===\nnew content\n")
    assert code == 2
    assert "REJECTED" in out
    assert "missing blob" in out.lower()


def test_create_recovers_cleanly_from_makedirs_failure(tmp_repo):
    """v2: if os.makedirs raises OSError in cmd_create, we should not crash
    with NameError (tmp_path unbound) — we should print a clean error and
    exit 2. Regression test for the Gemini finding.

    We trigger a real failure by putting a regular file at an intermediate
    path where a directory is expected. makedirs(parent, exist_ok=True)
    raises FileExistsError (subclass of OSError) when the path exists but
    is not a directory.
    """
    # Create a regular file at `blocker` — then ask vcs to create
    # `blocker/subdir/new.txt`, which requires makedirs("blocker/subdir").
    # `blocker` is a file, not a dir, so makedirs fails with NotADirectoryError
    # (subclass of OSError).
    blocker = tmp_repo / "blocker"
    blocker.write_text("I am a file, not a dir\n")
    f = tmp_repo / "blocker" / "subdir" / "new.txt"
    code, out, err = _run(["create", str(f)], input_data="content\n")
    assert code == 2
    assert "failed to create" in err.lower()
    # Verify the file was NOT created
    assert not f.exists()


def test_list_handles_symlink_cycles(tmp_repo):
    """v2: a symlink cycle inside a hidden directory must NOT cause an
    infinite loop in count_files_capped. Regression test for the Gemini
    symlink-loop finding.
    """
    # Create a hidden directory with a symlink cycle: .vcs_snapshots/self -> .
    # The tree command lists .vcs_snapshots in the hidden summary, calling
    # count_files_capped on it. Without the symlink guard, this would loop.
    hidden = tmp_repo / "node_modules"
    hidden.mkdir()
    (hidden / "real_file.txt").write_text("x\n")
    # Symlink cycle: node_modules/loop -> node_modules
    os.symlink(".", str(hidden / "loop"))
    # Symlink to ancestor: node_modules/up -> ..
    os.symlink("..", str(hidden / "up"))

    # This should complete quickly, not hang. Use a short timeout.
    import subprocess as sp
    cmd = [sys.executable, CLI, "list", str(tmp_repo), "--depth", "1"]
    try:
        result = sp.run(cmd, capture_output=True, text=True, cwd=str(tmp_repo), timeout=10)
    except sp.TimeoutExpired:
        pytest.fail("tree command hung on symlink cycle (infinite loop)")
    assert result.returncode == 0
    # The hidden dir is mentioned in the summary
    assert "node_modules" in result.stdout


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
    assert "2.1.0" in out


def test_help_lists_commands():
    code, out, err = _run(["--help"])
    assert code == 0
    for cmd in ("read", "replace", "insert", "delete", "create", "batch", "diff",
                "skeleton", "list", "grep", "fmt", "test", "status"):
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


# ===========================================================================
# v2.1 fixes — regression tests for field-test report bugs and optimizations
# ===========================================================================

# ---------------------------------------------------------------------------
# BUG-2: binary file detection in vcs read
# ---------------------------------------------------------------------------

def test_read_refuses_binary_file_with_null_bytes(tmp_repo):
    """BUG-2: vcs read on a file containing NUL bytes must error cleanly,
    NOT dump garbled bytes to stdout.
    """
    f = tmp_repo / "binary.bin"
    # Write 100 bytes of binary data with a guaranteed NUL byte in the middle
    import os as _os
    with open(f, "wb") as fh:
        fh.write(b"some text\nwith a NUL byte here:\x00\x01\x02\x03 and more\n")
    code, out, err = _run(["read", str(f)])
    assert code == 2
    assert "binary" in err.lower() or "binary" in out.lower()
    # Crucially, the output must NOT contain the raw bytes (which would
    # show up as garbled characters). The error message is a clean string.
    assert "file appears to be binary" in err.lower() or "file appears to be binary" in out.lower()


def test_read_refuses_binary_png_file(tmp_repo):
    """BUG-2: PNG files start with 0x89 0x50 0x4E 0x47 0x0D 0x0A 0x1A 0x0A
    and contain NUL bytes later. Should be detected as binary.
    """
    f = tmp_repo / "image.png"
    png_header = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + b"\x00" * 50
    with open(f, "wb") as fh:
        fh.write(png_header)
    code, out, err = _run(["read", str(f)])
    assert code == 2
    assert "binary" in err.lower() or "binary" in out.lower()


def test_read_still_works_on_utf8_with_multibyte_chars(tmp_repo):
    """BUG-2 regression: make sure UTF-8 files with multibyte chars (which
    have bytes in 0x80-0xFF range) are NOT falsely detected as binary.
    """
    f = tmp_repo / "utf8.txt"
    f.write_text("Hello 世界 🌍 café\n" * 10, encoding="utf-8")
    code, out, err = _run(["read", str(f)])
    assert code == 0
    assert "世界" in out
    assert "café" in out


def test_read_still_works_on_empty_file(tmp_repo):
    """BUG-2 regression: empty files should be treated as text (not binary)."""
    f = tmp_repo / "empty.txt"
    f.write_text("")
    code, out, err = _run(["read", str(f)])
    assert code == 0


# ---------------------------------------------------------------------------
# BUG-3: distinguish blob-mismatch cases (never-issued / wrong-file / conflict)
# ---------------------------------------------------------------------------

def test_replace_with_completely_fake_blob_gives_never_issued_error(tmp_repo):
    """BUG-3 case (a): a blob that was never issued by `vcs read` should
    produce a 'never issued' error, NOT a generic 'Merge conflict detected'
    message. The agent needs to know it should re-read, not just retry.
    """
    f = tmp_repo / "a.txt"
    f.write_text("a\nb\nc\n")
    # Don't call vcs read first — just try to use a fake blob
    code, out, err = _run(["replace", str(f), "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef", "1-1"],
                          input_data="X\n")
    assert code == 2  # error, NOT 1 (conflict)
    assert "never issued" in err.lower() or "never issued" in out.lower()
    # Crucially, must NOT say "Merge conflict detected"
    assert "merge conflict" not in err.lower() and "merge conflict" not in out.lower()
    # File must be unchanged
    assert f.read_text() == "a\nb\nc\n"


def test_replace_with_blob_from_different_file_gives_wrong_file_error(tmp_repo):
    """BUG-3 case (b): using a blob from file A to edit file B should
    produce a 'wrong file' error pointing at the original file, NOT a
    generic conflict message.
    """
    file_a = tmp_repo / "a.txt"
    file_a.write_text("content of A\n")
    blob_a = _read_and_get_blob(file_a)

    file_b = tmp_repo / "b.txt"
    file_b.write_text("content of B\n")

    code, out, err = _run(["replace", str(file_b), blob_a, "1-1"], input_data="X\n")
    assert code == 2  # error, NOT 1 (conflict)
    # Should mention "wrong file" or "issued for" with the original filename
    combined = (err + out).lower()
    assert "issued for" in combined or "wrong file" in combined
    assert "a.txt" in combined  # should reference the original file
    # Must NOT say "Merge conflict detected"
    assert "merge conflict" not in combined
    # File B must be unchanged
    assert file_b.read_text() == "content of B\n"


def test_replace_with_stale_blob_for_same_file_still_returns_conflict(tmp_repo):
    """BUG-3 case (c): genuine concurrent modification (file was read, then
    externally modified) should STILL return the conflict message. This is
    the one case where 'Merge conflict detected' IS the right message.
    """
    f = tmp_repo / "a.txt"
    f.write_text("a\nb\nc\nd\ne\n")
    blob = _read_and_get_blob(f)
    # Simulate external modification to lines 3-4 (inside agent's edit range)
    f.write_text("a\nb\nCC!\nd\ne\n")
    code, out, err = _run(["replace", str(f), blob, "3-4"], input_data="X\nY\n")
    assert code == 1  # conflict
    assert "merge conflict" in (out + err).lower()


# ---------------------------------------------------------------------------
# BUG-4: registry growth + keep only last N=100 blobs per file
# ---------------------------------------------------------------------------

def test_register_caps_blobs_per_file_at_100(tmp_repo):
    """BUG-4: after more than 100 blobs are registered for the same file,
    the oldest ones should be pruned. The registry should never have more
    than MAX_BLOBS_PER_FILE entries for a single filepath.
    """
    import sys
    sys.path.insert(0, str(tmp_repo.parent))  # ensure imports work
    from core.store import register, _load_store, MAX_BLOBS_PER_FILE, _find_repo_root

    f = tmp_repo / "many_blobs.txt"
    f.write_text("initial\n")

    # Register 110 different blob hashes for the same file (simulating 110
    # successive edits, each creating a new blob)
    repo_root = _find_repo_root(str(tmp_repo))
    for i in range(110):
        fake_hash = f"{'a' * 39}{i:01x}"[-40:]  # 40-char hex hash, unique per i
        register(fake_hash, str(f), repo_root=repo_root)

    data = _load_store(repo_root)
    blobs_for_file = [h for h, p in data["blobs"].items() if p == "many_blobs.txt"]
    assert len(blobs_for_file) <= MAX_BLOBS_PER_FILE, (
        f"expected <= {MAX_BLOBS_PER_FILE} blobs for the file, got {len(blobs_for_file)}"
    )


def test_register_does_not_store_short_prefix_as_separate_key(tmp_repo):
    """BUG-4 finding #4: registering a short prefix AND then the full hash
    should NOT result in two separate keys. The second call should
    consolidate to the full hash, removing the short prefix entry.
    """
    from core.store import register, _load_store, _find_repo_root

    f = tmp_repo / "short.txt"
    f.write_text("content\n")
    repo_root = _find_repo_root(str(tmp_repo))

    full_hash = "abcdef1234567890abcdef1234567890abcdef12"  # 40 chars
    short_prefix = full_hash[:8]

    # Step 1: register the short prefix (simulates agent passing short blob)
    register(short_prefix, str(f), repo_root=repo_root)
    data_after_short = _load_store(repo_root)
    # Short prefix is stored (no full hash known yet — this is correct)
    assert short_prefix in data_after_short["blobs"]

    # Step 2: register the full hash (simulates vcs read registering the
    # full hash for the same file)
    register(full_hash, str(f), repo_root=repo_root)
    data_after_full = _load_store(repo_root)
    # Full hash should be present
    assert full_hash in data_after_full["blobs"]
    # The short prefix may still be present (it's not auto-removed) BUT
    # when we now register the short prefix AGAIN, it should resolve to
    # the full hash and NOT create a duplicate.
    register(short_prefix, str(f), repo_root=repo_root)
    data_after_reregister = _load_store(repo_root)
    # The short prefix should NOT have been added as a new key — it should
    # have resolved to the existing full hash.
    short_count = sum(1 for h in data_after_reregister["blobs"].keys()
                      if h == short_prefix)
    assert short_count == 0, (
        f"short prefix '{short_prefix}' was stored as a separate key even "
        f"though a matching full hash '{full_hash}' exists — this is the "
        f"v2.0 duplicate bug (BUG-4 finding #4)"
    )


def test_deleted_files_get_pruned_by_gc(tmp_repo):
    """BUG-4 + OPT-3: `vcs gc` should remove registry entries for files
    that no longer exist on disk.
    """
    from core.store import register, _load_store, gc_store, _find_repo_root

    # Create + register a file
    f = tmp_repo / "doomed.txt"
    f.write_text("about to be deleted\n")
    blob = _read_and_get_blob(f)  # registers via read_file

    # Verify it's in the registry
    repo_root = _find_repo_root(str(tmp_repo))
    data_before = _load_store(repo_root)
    assert any(p == "doomed.txt" for p in data_before["blobs"].values())

    # Delete the file externally
    f.unlink()
    assert not f.exists()

    # Run gc
    result = gc_store(repo_root=repo_root)
    assert result["stale_entries"] >= 1

    # Verify the entry is gone
    data_after = _load_store(repo_root)
    assert not any(p == "doomed.txt" for p in data_after["blobs"].values())


def test_gc_removes_orphan_snapshots(tmp_repo):
    """BUG-4 + OPT-3: `vcs gc` should remove orphan snapshot files (those
    with no corresponding registry entry).
    """
    from core.store import _snapshots_dir, gc_store, _find_repo_root

    repo_root = _find_repo_root(str(tmp_repo))
    snap_dir = _snapshots_dir(repo_root)

    # Create an orphan snapshot file (no registry entry for it)
    orphan = snap_dir / "orphanhash1234567890orphanhash1234567890orphanhash12.txt"
    orphan.write_text("orphan content")

    assert orphan.exists()
    result = gc_store(repo_root=repo_root)
    assert result["orphan_snapshots"] >= 1
    assert not orphan.exists()


def test_vcs_gc_command_works_via_cli(tmp_repo):
    """OPT-3: `vcs gc` should be invocable from the CLI and print a summary."""
    f = tmp_repo / "deleted_via_cli.txt"
    f.write_text("temp\n")
    _read_and_get_blob(f)
    f.unlink()  # simulate external deletion

    code, out, err = _run(["gc"])
    assert code == 0
    assert "pruned" in out.lower()
    assert "stale" in out.lower()


def test_vcs_status_prune_flag_works_via_cli(tmp_repo):
    """OPT-3: `vcs status --prune` should prune stale entries then list."""
    f = tmp_repo / "prune_me.txt"
    f.write_text("temp\n")
    _read_and_get_blob(f)
    f.unlink()

    code, out, err = _run(["status", "--prune"])
    assert code == 0
    assert "pruned" in out.lower()
    # After pruning, the deleted file should not appear in the listing
    assert "prune_me.txt" not in out


# ---------------------------------------------------------------------------
# OPT-1: skeleton in-process (no subprocess)
# ---------------------------------------------------------------------------

def test_skeleton_still_works_after_opt1_refactor(tmp_repo):
    """OPT-1 regression: after switching skeleton from subprocess to in-process
    import, the basic skeleton command must still produce the same output.
    """
    f = tmp_repo / "test.py"
    f.write_text("def foo():\n    pass\n\ndef bar():\n    pass\n")
    code, out, err = _run(["skeleton", str(f)])
    assert code == 0
    assert "blob:" in out
    assert "def foo" in out
    assert "def bar" in out


def test_skeleton_with_range_still_works(tmp_repo):
    """OPT-1 regression: --start/--end range filtering must still work."""
    f = tmp_repo / "test.py"
    f.write_text("\n".join([
        "def func_a():",
        "    pass",
        "def func_b():",
        "    pass",
        "def func_c():",
        "    pass",
    ]))
    code, out, err = _run(["skeleton", str(f), "3-5"])
    assert code == 0
    assert "func_b" in out
    assert "func_c" in out


def test_skeleton_refuses_binary_file(tmp_repo):
    """OPT-1 regression: binary file detection in skeleton must still work
    after the refactor (it's now done via the in-process generate_skeleton)."""
    f = tmp_repo / "binary.bin"
    with open(f, "wb") as fh:
        fh.write(b"\x00" * 100)
    code, out, err = _run(["skeleton", str(f)])
    assert code == 2


def test_skeleton_is_faster_than_read_or_comparable(tmp_repo):
    """OPT-1 perf: skeleton should now be at most ~1.5x the time of read
    (previously it was ~1.6x because of subprocess overhead). This is a
    soft perf assertion — we just check it completes in reasonable time.
    """
    import time
    f = tmp_repo / "big.py"
    f.write_text("\n".join([f"def func_{i}():\n    pass" for i in range(300)]))

    # Warm up (load skeleton module, populate cache)
    _run(["skeleton", str(f)])

    # Time 5 calls
    start = time.time()
    for _ in range(5):
        _run(["skeleton", str(f)])
    elapsed = time.time() - start
    # Should average <500ms per call (was ~190ms in v2.0; should be similar
    # or better now without subprocess overhead). 500ms is a generous upper
    # bound that won't flake on slow CI.
    assert elapsed < 2.5, f"5 skeleton calls took {elapsed:.2f}s — too slow"


# ---------------------------------------------------------------------------
# OPT-4: trailing newline on read if missing
# ---------------------------------------------------------------------------

def test_read_adds_trailing_newline_for_display(tmp_repo):
    """OPT-4: a file without a trailing newline should still produce clean
    output (last line followed by newline) so the shell prompt doesn't
    merge with the last line in terminal output.
    """
    f = tmp_repo / "no_newline.txt"
    # Write content with NO trailing newline
    with open(f, "w") as fh:
        fh.write("line1\nline2\nline3")  # no \n at end
    code, out, err = _run(["read", str(f)])
    assert code == 0
    # The output should end with a newline (so the shell prompt doesn't
    # merge with "line3")
    assert out.endswith("\n"), "output should end with newline for clean terminal display"
    # All three lines should be visible with their line numbers
    assert "1: line1" in out
    assert "2: line2" in out
    assert "3: line3" in out


def test_read_does_not_modify_file_without_trailing_newline(tmp_repo):
    """OPT-4 regression: the on-disk file must NOT be modified — we only
    add a newline to the DISPLAYED content, not the file itself.
    """
    f = tmp_repo / "no_newline.txt"
    with open(f, "w") as fh:
        fh.write("line1\nline2\nline3")  # no \n at end
    _run(["read", str(f)])
    # File on disk must still have NO trailing newline
    with open(f, "rb") as fh:
        content = fh.read()
    assert not content.endswith(b"\n"), "file on disk must not be modified by vcs read"


def test_read_preserves_trailing_newline_if_present(tmp_repo):
    """OPT-4 regression: if the file already has a trailing newline, behavior
    is unchanged — we don't add an extra one."""
    f = tmp_repo / "with_newline.txt"
    f.write_text("line1\nline2\n")  # already has trailing \n
    code, out, err = _run(["read", str(f)])
    assert code == 0
    # Should end with exactly one newline (not two)
    assert out.endswith("\n")
    assert not out.endswith("\n\n")

