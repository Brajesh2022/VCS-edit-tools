"""Tests for core/blob.py."""
import subprocess
from pathlib import Path

from core.blob import get_blob_hash, find_file_by_blob


def test_get_blob_hash_matches_git(tmp_path):
    """Our pure-python blob hash must match `git hash-object` exactly."""
    f = tmp_path / "x.txt"
    f.write_text("hello world\n")
    ours = get_blob_hash(str(f))
    git = subprocess.run(
        ["git", "hash-object", str(f)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert ours == git


def test_get_blob_hash_is_deterministic(tmp_path):
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_text("same content\n")
    f2.write_text("same content\n")
    assert get_blob_hash(str(f1)) == get_blob_hash(str(f2))


def test_get_blob_hash_changes_with_content(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("v1\n")
    h1 = get_blob_hash(str(f))
    f.write_text("v2\n")
    h2 = get_blob_hash(str(f))
    assert h1 != h2


def test_get_blob_hash_missing_file(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):
        get_blob_hash(str(tmp_path / "nope.txt"))


def test_find_file_by_blob_walk(tmp_path):
    """find_file_by_blob should locate a file by its content hash via filesystem walk."""
    f = tmp_path / "deep" / "nested" / "x.py"
    f.parent.mkdir(parents=True)
    f.write_text("find me\n")
    h = get_blob_hash(str(f))
    found = find_file_by_blob(h, search_root=str(tmp_path))
    assert found is not None
    assert Path(tmp_path / found).resolve() == f.resolve()


def test_find_file_by_blob_short_prefix(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("prefix test\n")
    h = get_blob_hash(str(f))
    short = h[:8]
    found = find_file_by_blob(short, search_root=str(tmp_path))
    assert found is not None


def test_find_file_by_blob_returns_none_for_unknown(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("hello\n")
    assert find_file_by_blob("0" * 40, search_root=str(tmp_path)) is None
