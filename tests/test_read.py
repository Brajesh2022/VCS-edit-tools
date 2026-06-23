"""Tests for core/read.py."""
import pytest

from core.read import read_file, MAX_READ_LINES


def test_read_basic_returns_blob_and_content(sample_file):
    r = read_file(str(sample_file))
    assert "blob" in r
    assert r["path"] == str(sample_file)
    assert r["total_lines"] == 5
    assert r["shown_range"] == "1-5"
    assert r["truncated"] == 0
    assert r["next_command"] is None
    assert r["content"] == "1: line1\n2: line2\n3: line3\n4: line4\n5: line5\n"


def test_read_blob_is_full_file_hash(sample_file):
    """The blob hash always represents the WHOLE file, not the visible window."""
    full = read_file(str(sample_file))
    windowed = read_file(str(sample_file), 2, 4)
    assert full["blob"] == windowed["blob"]


def test_read_partial_range(sample_file):
    r = read_file(str(sample_file), 2, 4)
    assert r["shown_range"] == "2-4"
    assert r["content"] == "2: line2\n3: line3\n4: line4\n"
    assert r["truncated"] == 0


def test_read_single_line_range(sample_file):
    r = read_file(str(sample_file), 3, 3)
    assert r["shown_range"] == "3-3"
    assert r["content"] == "3: line3\n"


def test_read_open_ended_end(sample_file):
    r = read_file(str(sample_file), 3, None)
    assert r["shown_range"] == "3-5"
    assert r["content"] == "3: line3\n4: line4\n5: line5\n"


def test_read_truncation_at_800_lines(tmp_repo):
    f = tmp_repo / "big.txt"
    f.write_text("".join(f"line {i}\n" for i in range(1, 1201)))
    r = read_file(str(f))
    assert r["total_lines"] == 1200
    assert r["shown_range"] == "1-800"
    assert r["truncated"] == 400
    assert r["next_command"] is not None
    assert "801-1200" in r["next_command"]


def test_read_window_cap_at_max_lines(tmp_repo):
    f = tmp_repo / "big.txt"
    f.write_text("".join(f"line {i}\n" for i in range(1, 1201)))
    # Request 100-1000 (901 lines, > MAX).  Must show exactly MAX lines.
    r = read_file(str(f), 100, 1000)
    assert r["shown_range"] == "100-899"  # 800 lines
    assert r["truncated"] == 101          # 1000-899 = 101
    assert r["next_command"] is not None
    assert "900-1000" in r["next_command"]


def test_read_clamps_past_eof(sample_file):
    r = read_file(str(sample_file), 3, 999)
    assert r["shown_range"] == "3-5"
    assert r["content"] == "3: line3\n4: line4\n5: line5\n"
    assert r["truncated"] == 0


def test_read_clamps_negative_start(sample_file):
    r = read_file(str(sample_file), -5, 2)
    assert r["shown_range"] == "1-2"
    assert r["content"] == "1: line1\n2: line2\n"


def test_read_missing_file_raises(tmp_repo):
    with pytest.raises(FileNotFoundError):
        read_file(str(tmp_repo / "nope.txt"))


def test_read_directory_raises(tmp_repo):
    with pytest.raises(IsADirectoryError):
        read_file(str(tmp_repo))


def test_read_registers_blob_in_store(sample_file):
    from core.store import lookup
    r = read_file(str(sample_file))
    assert lookup(r["blob"]) is not None


def test_read_snapshots_content_for_conflict_resolution(sample_file):
    """read() must save a snapshot of the file content so the 3-way merge
    has access to `base` even if the file is later modified.
    """
    from core.store import load_snapshot
    r = read_file(str(sample_file))
    snap = load_snapshot(r["blob"])
    assert snap is not None
    assert snap == "line1\nline2\nline3\nline4\nline5\n"


def test_read_no_truncation_when_under_max(tmp_repo):
    f = tmp_repo / "med.txt"
    f.write_text("".join(f"line {i}\n" for i in range(1, 800)))  # 799 lines
    r = read_file(str(f))
    assert r["truncated"] == 0
    assert r["shown_range"] == f"1-799"
