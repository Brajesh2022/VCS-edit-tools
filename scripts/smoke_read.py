"""Quick smoke test for Phase 2 read_file behavior.

Covers every row of the spec table:
  | Call                       | File lines | Shows         | Truncation msg                       |
  | vcs read auth.js           | 600        | 1-600         | none                                 |
  | vcs read auth.js           | 1200       | 1-800         | 400 lines truncated. ...801-1200     |
  | vcs read auth.js 519-639   | any        | 519-639       | none                                 |
  | vcs read auth.js 100-1000  | any        | 100-900       | 100 lines truncated. ...901-1000     |
  | vcs read auth.js 1-330     | any        | 1-330         | none                                 |
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.read import read_file, MAX_READ_LINES


def make_file(n_lines: int) -> str:
    fd, path = tempfile.mkstemp(suffix=".py", dir=".", prefix="_smoke_")
    with os.fdopen(fd, "w") as f:
        for i in range(1, n_lines + 1):
            f.write(f"line {i}\n")
    return os.path.basename(path)


def check(label, got, expected_shown_range, expected_truncated, expected_next_contains=None):
    sr = got["shown_range"]
    tr = got["truncated"]
    nxt = got.get("next_command")
    ok = (sr == expected_shown_range) and (tr == expected_truncated)
    if expected_next_contains is not None:
        ok = ok and (nxt is not None) and (expected_next_contains in nxt)
    elif expected_truncated == 0:
        ok = ok and (nxt is None)
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {label}: shown_range={sr} truncated={tr} next={nxt}")
    if not ok:
        print(f"        expected: shown_range={expected_shown_range} truncated={expected_truncated} next~={expected_next_contains}")
    return ok


def main():
    all_pass = True

    # Row 1: 600 lines, default read → 1-600, no truncation
    p1 = make_file(600)
    all_pass &= check("600 lines default",
                      read_file(p1), "1-600", 0)

    # Row 2: 1200 lines, default read → 1-800, 400 truncated
    p2 = make_file(1200)
    all_pass &= check("1200 lines default",
                      read_file(p2), "1-800", 400, "801-1200")

    # Row 3: explicit range 519-639 (≤800) → 519-639
    all_pass &= check("519-639",
                      read_file(p2, 519, 639), "519-639", 0)

    # Row 4: 100-1000 (>800) → 100-899 (strict 800 lines), 101 truncated
    # Spec example "100-900" has an arithmetic slip (100-900 = 801 lines).
    # Design intent "Max window is always 800 lines" wins → 100-899, 101 truncated.
    all_pass &= check("100-1000 (strict 800-window)",
                      read_file(p2, 100, 1000), "100-899", 101, "900-1000")

    # Row 5: 1-330 (≤800) → 1-330, no truncation
    all_pass &= check("1-330",
                      read_file(p2, 1, 330), "1-330", 0)

    # Edge: range past EOF clamps
    p3 = make_file(50)
    all_pass &= check("100-200 on 50-line file (clamp)",
                      read_file(p3, 100, 200), "50-50", 0)

    # Edge: blob hash is full-file hash even when window is partial
    import hashlib
    with open(p3, "rb") as fh:
        data = fh.read()
    full_hash = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
    r = read_file(p3, 5, 10)
    ok = r["blob"] == full_hash
    print(f"[{'PASS' if ok else 'FAIL'}] blob hash matches full file (not window)")
    all_pass &= ok

    # Cleanup
    for p in [p1, p2, p3]:
        os.remove(p)
    store = ".vcs_store.json"
    if os.path.exists(store):
        os.remove(store)

    print("\nALL PASS" if all_pass else "\nSOME FAILED")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
