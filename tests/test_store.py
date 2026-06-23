"""Tests for core/store.py."""
import json
from pathlib import Path

from core.store import (
    register, lookup, resolve_path,
    save_snapshot, load_snapshot, clear_store,
    _find_repo_root, _store_path,
)


def test_register_and_lookup(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("hello\n")
    register("abc123", str(f))
    assert lookup("abc123") is not None


def test_lookup_missing_returns_none(tmp_repo):
    assert lookup("nonexistent") is None


def test_lookup_short_prefix(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("hello\n")
    register("abcdef1234567890", str(f))
    assert lookup("abcdef") is not None
    assert lookup("abcde") is not None


def test_resolve_path_returns_path_even_after_file_modified(tmp_repo):
    """Critical: resolve_path must return the registered path even if the
    file has since been modified (this is the conflict-detection case).
    """
    f = tmp_repo / "a.txt"
    f.write_text("v1\n")
    register("hash1", str(f))
    # Modify the file
    f.write_text("v2\n")
    # resolve_path should still return the path (not None)
    p = resolve_path("hash1")
    assert p is not None
    assert "a.txt" in p


def test_resolve_path_falls_back_to_filesystem_walk(tmp_repo):
    """If not in registry, fall back to find_file_by_blob."""
    from core.blob import get_blob_hash
    f = tmp_repo / "a.txt"
    f.write_text("unique content\n")
    h = get_blob_hash(str(f))
    # Don't register; let filesystem walk find it
    p = resolve_path(h)
    assert p is not None
    assert "a.txt" in p


def test_snapshot_save_and_load(tmp_repo):
    content = "line1\nline2\nline3\n"
    save_snapshot("hash123", content)
    loaded = load_snapshot("hash123")
    assert loaded == content


def test_snapshot_load_missing_returns_none(tmp_repo):
    assert load_snapshot("nonexistent") is None


def test_snapshot_short_prefix(tmp_repo):
    save_snapshot("abcdef1234567890", "x\n")
    assert load_snapshot("abcdef") == "x\n"


def test_clear_store_wipes_everything(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("v1\n")
    register("hash1", str(f))
    save_snapshot("hash1", "v1\n")
    clear_store()
    assert lookup("hash1") is None
    assert load_snapshot("hash1") is None


def test_store_json_persists_across_calls(tmp_repo):
    f = tmp_repo / "a.txt"
    f.write_text("hello\n")
    register("persistent_hash", str(f))
    # The .vcs_store.json file should exist on disk
    assert _store_path(str(tmp_repo)).exists()
    # And lookup should find it on a fresh load (no in-memory cache)
    data = json.loads(_store_path(str(tmp_repo)).read_text())
    assert "persistent_hash" in data["blobs"]


def test_find_repo_root_finds_git_dir(tmp_repo):
    # tmp_repo fixture sets up a .git directory
    nested = tmp_repo / "deep" / "nested"
    nested.mkdir(parents=True)
    root = _find_repo_root(str(nested))
    assert Path(root).resolve() == tmp_repo.resolve()
