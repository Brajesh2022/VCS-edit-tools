"""Smoke test for Phase 3/4: replace.py + conflict.py.

Scenarios:
  1. Happy path: replace lines 3-4 in a 6-line file → clean.
  2. Conflict (non-overlapping): file changed ABOVE the edit range → auto-merge.
  3. Conflict (overlapping): file changed INSIDE the edit range → conflict report.
  4. Single-line range '8-8' works.
  5. Open-ended range '5-' to end of file.
  6. Replace with longer content (insertion via replace).
  7. Replace with shorter content (deletion via replace).
"""
import os
import sys
import tempfile

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)
os.chdir(PROJECT)

from core.read import read_file
from core.replace import replace, parse_line_range
from core.store import clear_store


def write_tmp(content: str) -> str:
    fd, path = tempfile.mkstemp(prefix="vcs_new_", suffix=".txt")
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return path


def write_repo_file(name: str, content: str) -> str:
    p = os.path.join(PROJECT, name)
    with open(p, "w") as f:
        f.write(content)
    return p


def check(label, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    return cond


def cleanup(*paths):
    for p in paths:
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass
    clear_store()


def main():
    all_pass = True

    # --- Scenario 1: happy path ---
    f1 = write_repo_file("_t1.txt", "line1\nline2\nline3\nline4\nline5\nline6\n")
    r = read_file(f1)
    blob = r["blob"]
    new_content = write_tmp("REPLACED\n")
    result = replace(blob, "3-4", new_content)
    all_pass &= check("1. happy path status=ok", result["status"] == "ok",
                      f"got {result.get('status')}")
    with open(f1) as fh:
        after = fh.read()
    expected = "line1\nline2\nREPLACED\nline5\nline6\n"
    all_pass &= check("1. happy path content matches", after == expected,
                      f"\nexpected={expected!r}\n     got={after!r}")
    cleanup(f1, new_content)

    # --- Scenario 2: non-overlapping auto-merge ---
    # Initial: 6 lines.  Agent reads, gets blob A.
    # File then changes ABOVE the agent's range (line 1 modified).
    # Agent calls replace on lines 4-5 → should auto-merge cleanly.
    f2 = write_repo_file("_t2.txt", "alpha\nb\nc\nd\ne\nf\n")
    r = read_file(f2)
    blob = r["blob"]
    # Now modify line 1 (above the agent's edit range of 4-5)
    write_repo_file("_t2.txt", "ALPHA\nb\nc\nd\ne\nf\n")
    new_content = write_tmp("DD\nEE\n")
    result = replace(blob, "4-5", new_content)
    all_pass &= check("2. non-overlap status=auto_merged",
                      result["status"] == "auto_merged",
                      f"got {result.get('status')}, detail={result}")
    with open(f2) as fh:
        after = fh.read()
    # Expected: ALPHA b c DD EE f  (both edits applied)
    expected = "ALPHA\nb\nc\nDD\nEE\nf\n"
    all_pass &= check("2. non-overlap content matches", after == expected,
                      f"\nexpected={expected!r}\n     got={after!r}")
    cleanup(f2, new_content)

    # --- Scenario 3: overlapping conflict ---
    # Initial: 6 lines. Agent reads, gets blob A.
    # File then changes INSIDE the agent's range.
    # Agent calls replace on lines 3-4 → should report a conflict.
    f3 = write_repo_file("_t3.txt", "a\nb\nc\nd\ne\nf\n")
    r = read_file(f3)
    blob = r["blob"]
    # Modify line 3 (inside agent's edit range of 3-4)
    write_repo_file("_t3.txt", "a\nb\nCHANGED_C\nd\ne\nf\n")
    new_content = write_tmp("NEW_C\nNEW_D\n")
    result = replace(blob, "3-4", new_content)
    all_pass &= check("3. overlap status=conflict",
                      result["status"] == "conflict",
                      f"got {result.get('status')}, detail={result}")
    if result["status"] == "conflict":
        all_pass &= check("3. overlap has diff", bool(result.get("diff")),
                          f"diff={result.get('diff')!r}")
        all_pass &= check("3. overlap conflicting_lines set",
                          bool(result.get("conflicting_lines")))
        # File should NOT have been modified
        with open(f3) as fh:
            after = fh.read()
        all_pass &= check("3. file unchanged on conflict",
                          after == "a\nb\nCHANGED_C\nd\ne\nf\n",
                          f"got {after!r}")
    cleanup(f3, new_content)

    # --- Scenario 4: single-line range ---
    f4 = write_repo_file("_t4.txt", "a\nb\nc\nd\n")
    r = read_file(f4)
    blob = r["blob"]
    new_content = write_tmp("B!\n")
    result = replace(blob, "2-2", new_content)
    all_pass &= check("4. single-line status=ok", result["status"] == "ok")
    with open(f4) as fh:
        after = fh.read()
    all_pass &= check("4. single-line content", after == "a\nB!\nc\nd\n",
                      f"got {after!r}")
    cleanup(f4, new_content)

    # --- Scenario 5: open-ended range '3-' to EOF ---
    f5 = write_repo_file("_t5.txt", "a\nb\nc\nd\ne\n")
    r = read_file(f5)
    blob = r["blob"]
    new_content = write_tmp("X\nY\n")
    result = replace(blob, "3-", new_content)
    all_pass &= check("5. open-ended status=ok", result["status"] == "ok",
                      f"detail={result}")
    with open(f5) as fh:
        after = fh.read()
    all_pass &= check("5. open-ended content", after == "a\nb\nX\nY\n",
                      f"got {after!r}")
    cleanup(f5, new_content)

    # --- Scenario 6: replace with longer content (net insertion) ---
    f6 = write_repo_file("_t6.txt", "a\nb\nc\n")
    r = read_file(f6)
    blob = r["blob"]
    new_content = write_tmp("B1\nB2\nB3\n")
    result = replace(blob, "2-2", new_content)
    all_pass &= check("6. net insertion status=ok", result["status"] == "ok")
    with open(f6) as fh:
        after = fh.read()
    all_pass &= check("6. net insertion content",
                      after == "a\nB1\nB2\nB3\nc\n", f"got {after!r}")
    cleanup(f6, new_content)

    # --- Scenario 7: replace with shorter content (net deletion) ---
    f7 = write_repo_file("_t7.txt", "a\nb\nc\nd\ne\n")
    r = read_file(f7)
    blob = r["blob"]
    new_content = write_tmp("X\n")
    result = replace(blob, "2-4", new_content)
    all_pass &= check("7. net deletion status=ok", result["status"] == "ok")
    with open(f7) as fh:
        after = fh.read()
    all_pass &= check("7. net deletion content",
                      after == "a\nX\ne\n", f"got {after!r}")
    cleanup(f7, new_content)

    # --- Scenario 8: blob mismatch with no base in git (untracked change) ---
    # This tests the fallback "soft conflict" path where we can't reconstruct base
    f8 = write_repo_file("_t8.txt", "a\nb\nc\n")
    r = read_file(f8)
    blob = r["blob"]
    write_repo_file("_t8.txt", "a\nB!\nc\n")  # modify in place
    new_content = write_tmp("X\nY\n")
    result = replace(blob, "2-2", new_content)
    # File _is_ tracked by git (since we did git add earlier on the project),
    # but this specific file _t8.txt is untracked, so git cat-file will fail.
    # Expect soft conflict.
    all_pass &= check("8. soft conflict (no base) status=conflict",
                      result["status"] == "conflict",
                      f"detail={result}")
    if result["status"] == "conflict":
        all_pass &= check("8. soft conflict has their_change",
                          "B!" in (result.get("their_change") or ""))
    cleanup(f8, new_content)

    print()
    print("ALL PASS" if all_pass else "SOME FAILED")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
