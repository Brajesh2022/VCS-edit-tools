#!/usr/bin/env bash
# End-to-end smoke test for the VCS CLI wrapper.
# Drives `./vcs` as a subprocess the way an AI agent would.
#
# v2 API: replace/insert/delete(line)/batch require BOTH filepath AND blob.
set -u

# Resolve the vcs wrapper path relative to this script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
VCS="$PROJECT_DIR/vcs"
TEST_DIR=$(mktemp -d)
cd "$TEST_DIR" || exit 99
git init -q
git config user.email t@t
git config user.name t

PASS=0
FAIL=0
ok(){ echo "[PASS] $1"; PASS=$((PASS+1)); }
no(){ echo "[FAIL] $1 — $2"; FAIL=$((FAIL+1)); }

# Helper: extract blob from `vcs read` output (first 'blob:' line, second field)
get_blob() {
    grep '^blob:' | awk '{print $2}'
}

# Clean state
rm -rf .vcs_store.json .vcs_snapshots/ _e2e_*.txt 2>/dev/null

# ---------- Test 1: read a file → returns blob in output ----------
cat > _e2e_a.txt << 'PY'
def hello():
    print("hello")

def world():
    print("world")
PY

OUT=$($VCS read _e2e_a.txt)
BLOB=$(echo "$OUT" | get_blob)
if [[ -n "$BLOB" ]]; then ok "read returns blob"; else no "read" "no blob in: $OUT"; fi
BLOB_SHORT=${BLOB:0:8}
echo "    blob=$BLOB  short=$BLOB_SHORT"

# ---------- Test 2: exit code 0 on success ----------
$VCS read _e2e_a.txt > /dev/null
EC=$?
if [[ $EC -eq 0 ]]; then ok "read exit code 0"; else no "read exit code" "got $EC"; fi

# ---------- Test 3: read non-existent file → exit 2 ----------
OUT=$($VCS read _e2e_does_not_exist.txt 2>&1)
EC=$?
if [[ $EC -eq 2 ]]; then ok "read missing file → exit 2"; else no "read missing file" "exit=$EC out=$OUT"; fi

# ---------- Test 4: replace happy path (v2: filepath + blob + range) ----------
OUT=$($VCS replace _e2e_a.txt "$BLOB" 2-3 << 'EOF'
REPLACED_LINE
EOF
)
EC=$?
if [[ $EC -eq 0 && "$OUT" == *"status: ok"* ]]; then ok "replace happy path"; else no "replace happy path" "ec=$EC out=$OUT"; fi

# Verify file content
AFTER=$(cat _e2e_a.txt)
EXPECTED='def hello():
REPLACED_LINE
def world():
    print("world")'
if [[ "$AFTER" == "$EXPECTED" ]]; then ok "replace content correct"; else no "replace content" "got=$AFTER"; fi

# ---------- Test 5: replace with stale blob + non-overlapping change → auto_merged ----------
OUT=$($VCS read _e2e_a.txt)
CUR_BLOB=$(echo "$OUT" | get_blob)
# Stale the blob: edit line 1 outside of agent's view
sed -i 's/def hello():/def hello_changed():/' _e2e_a.txt
# Now try to replace line 4 using the OLD blob — should auto-merge (clean status: ok)
OUT=$($VCS replace _e2e_a.txt "$CUR_BLOB" 4-4 << 'EOF'
WORLD_REPLACED
EOF
)
EC=$?
if [[ $EC -eq 0 && "$OUT" == *"status: ok"* ]]; then ok "non-overlapping conflict → auto_merged (status: ok)"; else no "auto-merge" "ec=$EC out=$OUT"; fi

AFTER=$(cat _e2e_a.txt)
EXPECTED='def hello_changed():
REPLACED_LINE
def world():
WORLD_REPLACED'
if [[ "$AFTER" == "$EXPECTED" ]]; then ok "auto-merge content correct"; else no "auto-merge content" "got=$AFTER"; fi

# ---------- Test 6: overlapping conflict → exit 1 with simple message ----------
OUT=$($VCS read _e2e_a.txt)
CUR_BLOB=$(echo "$OUT" | get_blob)
# Modify line 4 (inside agent's edit range of 4-4) — should report conflict
sed -i '4s/.*/CONCURRENT_CHANGE/' _e2e_a.txt
OUT=$($VCS replace _e2e_a.txt "$CUR_BLOB" 4-4 << 'EOF'
AGENT_REPLACE
EOF
)
EC=$?
if [[ $EC -eq 1 && "$OUT" == *"Merge conflict detected"* ]]; then ok "overlap conflict → exit 1 with simple message"; else no "overlap conflict" "ec=$EC out=$OUT"; fi
# Verify file was NOT modified by agent
AFTER=$(sed -n '4p' _e2e_a.txt)
if [[ "$AFTER" == "CONCURRENT_CHANGE" ]]; then ok "file unchanged on conflict"; else no "file unchanged" "line 4=$AFTER"; fi

# ---------- Test 7: insert command ----------
OUT=$($VCS read _e2e_a.txt)
CUR_BLOB=$(echo "$OUT" | get_blob)
OUT=$($VCS insert _e2e_a.txt "$CUR_BLOB" 2 << 'EOF'
INSERTED_LINE_1
INSERTED_LINE_2
EOF
)
EC=$?
if [[ $EC -eq 0 && "$OUT" == *"status: ok"* ]]; then ok "insert happy path"; else no "insert" "ec=$EC out=$OUT"; fi
L2=$(sed -n '2p' _e2e_a.txt)
L3=$(sed -n '3p' _e2e_a.txt)
if [[ "$L2" == "INSERTED_LINE_1" && "$L3" == "INSERTED_LINE_2" ]]; then ok "insert content correct"; else no "insert content" "L2=$L2 L3=$L3"; fi

# ---------- Test 8: delete line-range command (filepath + blob + range) ----------
OUT=$($VCS read _e2e_a.txt)
CUR_BLOB=$(echo "$OUT" | get_blob)
# Delete lines 2-3 (the inserted lines)
OUT=$($VCS delete _e2e_a.txt "$CUR_BLOB" 2-3)
EC=$?
if [[ $EC -eq 0 && "$OUT" == *"status: ok"* ]]; then ok "delete line-range happy path"; else no "delete" "ec=$EC out=$OUT"; fi
L2=$(sed -n '2p' _e2e_a.txt)
if [[ "$L2" != "INSERTED_LINE_1" ]]; then ok "delete content correct"; else no "delete content" "L2 still=$L2"; fi

# ---------- Test 8b: delete file command (just filepath) ----------
echo "to be deleted" > _e2e_delete_me.txt
OUT=$($VCS delete _e2e_delete_me.txt)
EC=$?
if [[ $EC -eq 0 && "$OUT" == *"status: ok"* && ! -f _e2e_delete_me.txt ]]; then ok "delete file works"; else no "delete file" "ec=$EC out=$OUT exists=$([ -f _e2e_delete_me.txt ] && echo yes || echo no)"; fi

# ---------- Test 8c: delete directory command (just filepath, recursive) ----------
mkdir -p _e2e_dir/nested
echo "x" > _e2e_dir/a.txt
echo "y" > _e2e_dir/nested/b.txt
OUT=$($VCS delete _e2e_dir)
EC=$?
if [[ $EC -eq 0 && "$OUT" == *"status: ok"* && ! -d _e2e_dir ]]; then ok "delete directory works"; else no "delete dir" "ec=$EC out=$OUT exists=$([ -d _e2e_dir ] && echo yes || echo no)"; fi

# ---------- Test 9: create command ----------
OUT=$($VCS create _e2e_created.txt << 'EOF'
line 1
line 2
EOF
)
EC=$?
if [[ $EC -eq 0 && "$OUT" == *"status: ok"* && -f _e2e_created.txt ]]; then ok "create works"; else no "create" "ec=$EC out=$OUT exists=$([ -f _e2e_created.txt ] && echo yes || echo no)"; fi
CONTENT=$(cat _e2e_created.txt)
if [[ "$CONTENT" == "line 1
line 2" ]]; then ok "create content correct"; else no "create content" "got=$CONTENT"; fi

# ---------- Test 10: create existing file → exit 2 ----------
OUT=$($VCS create _e2e_created.txt << 'EOF'
should fail
EOF
)
EC=$?
if [[ $EC -eq 2 ]]; then ok "create existing file → exit 2"; else no "create existing" "ec=$EC out=$OUT"; fi

# ---------- Test 11: batch with blob (happy path) ----------
echo -e "A\nB\nC\nD\nE" > _e2e_batch.txt
OUT=$($VCS read _e2e_batch.txt)
BBLOB=$(echo "$OUT" | get_blob)
OUT=$($VCS batch << EOF
[{"filepath":"_e2e_batch.txt","blob":"$BBLOB","type":"replace","line_range":"2-2","content":"BB!"}]
EOF
)
EC=$?
if [[ $EC -eq 0 && "$OUT" == *"1 ok"* ]]; then ok "batch with blob works"; else no "batch with blob" "ec=$EC out=$OUT"; fi

# ---------- Test 12: batch WITHOUT blob → rejected ----------
echo -e "A\nB\nC" > _e2e_batch2.txt
OUT=$($VCS batch << 'EOF'
[{"filepath":"_e2e_batch2.txt","type":"replace","line_range":"1-1","content":"X"}]
EOF
)
EC=$?
if [[ $EC -eq 2 && "$OUT" == *"REJECTED"* ]]; then ok "batch without blob → rejected"; else no "batch no blob" "ec=$EC out=$OUT"; fi

# ---------- Test 13: replace WITHOUT blob → exit 2 (v2 contract) ----------
echo -e "A\nB\nC" > _e2e_noblob.txt
OUT=$($VCS replace _e2e_noblob.txt 1-1 << 'EOF'
X
EOF
)
EC=$?
if [[ $EC -eq 2 ]]; then ok "replace without blob → exit 2 (v2 contract)"; else no "replace no blob" "ec=$EC out=$OUT"; fi

# ---------- Test 14: version flag ----------
OUT=$($VCS --version 2>&1)
if [[ "$OUT" == *"2.1.0"* ]]; then ok "--version works (v2.1.0)"; else no "--version" "out=$OUT"; fi

# ---------- Test 15: help flag lists commands ----------
OUT=$($VCS --help 2>&1)
if echo "$OUT" | grep -q "read" && echo "$OUT" | grep -q "replace" && echo "$OUT" | grep -q "create"; then ok "--help lists commands (incl. create)"; else no "--help" "out=$OUT"; fi

# ---------- Test 16: tree command does NOT mention 'agy-list' ----------
mkdir -p subdir
echo "x" > subdir/file.txt
OUT=$($VCS tree . --depth 1 2>&1)
if [[ "$OUT" != *"agy-list"* && "$OUT" == *"vcs list"* ]]; then ok "tree uses 'vcs list' (not agy-list)"; else no "tree naming" "out=$OUT"; fi

# ---------- Test 17 (v2.1): vcs read refuses binary files ----------
printf "text\x00binary\n" > _e2e_binary.bin
OUT=$($VCS read _e2e_binary.bin 2>&1)
EC=$?
if [[ $EC -eq 2 && "$OUT" == *"binary"* ]]; then ok "vcs read refuses binary (BUG-2)"; else no "binary refusal" "ec=$EC out=$OUT"; fi

# ---------- Test 18 (v2.1): vcs read accepts UTF-8 with multibyte chars ----------
echo "Hello 世界 café" > _e2e_utf8.txt
OUT=$($VCS read _e2e_utf8.txt 2>&1)
EC=$?
if [[ $EC -eq 0 && "$OUT" == *"世界"* ]]; then ok "vcs read accepts UTF-8 (BUG-2 regression)"; else no "utf8 acceptance" "ec=$EC out=$OUT"; fi

# ---------- Test 19 (v2.1): vcs replace with fake blob gives 'never issued' error ----------
echo "content" > _e2e_fake.txt
# Use a temp file for stdin to avoid heredoc+redirection ordering issues
echo "new" > _e2e_new.txt
OUT=$($VCS replace _e2e_fake.txt deadbeefdeadbeefdeadbeefdeadbeefdeadbeef 1-1 < _e2e_new.txt 2>&1)
EC=$?
if [[ $EC -eq 2 && "$OUT" == *"never issued"* ]]; then ok "fake blob gives never-issued error (BUG-3a)"; else no "fake blob" "ec=$EC out=$OUT"; fi

# ---------- Test 20 (v2.1): vcs read adds trailing newline for display ----------
printf "line1
line2
line3" > _e2e_no_newline.txt  # no trailing 

# $(...) strips trailing newlines, so check via wc -c on the raw output
RAW_OUT=$($VCS read _e2e_no_newline.txt 2>&1; printf "X")
# If the char before X is a newline, the output ended with newline
LAST_CHAR="${RAW_OUT: -2:1}"
if [[ "$LAST_CHAR" == $'
' ]]; then ok "vcs read adds trailing newline (OPT-4)"; else no "trailing newline" "last_char=$(printf '%q' "$LAST_CHAR")"; fi
# Verify file on disk is NOT modified
DISK_CONTENT=$(python3 -c "print(repr(open('_e2e_no_newline.txt').read()))")
if [[ "$DISK_CONTENT" == *"'line1\nline2\nline3'"* ]]; then ok "file on disk unchanged (OPT-4 regression)"; else no "disk unchanged" "disk=$DISK_CONTENT"; fi

# ---------- Test 21 (v2.1): vcs gc command works ----------
echo "stale" > _e2e_stale.txt
$VCS read _e2e_stale.txt > /dev/null 2>&1
rm _e2e_stale.txt
OUT=$($VCS gc 2>&1)
EC=$?
if [[ $EC -eq 0 && "$OUT" == *"pruned"* && "$OUT" == *"stale"* ]]; then ok "vcs gc works (OPT-3)"; else no "vcs gc" "ec=$EC out=$OUT"; fi

# ---------- Test 22 (v2.1): vcs status --prune flag works ----------
echo "stale2" > _e2e_stale2.txt
$VCS read _e2e_stale2.txt > /dev/null 2>&1
rm _e2e_stale2.txt
OUT=$($VCS status --prune 2>&1)
EC=$?
if [[ $EC -eq 0 && "$OUT" == *"pruned"* && "$OUT" != *"_e2e_stale2.txt"* ]]; then ok "vcs status --prune works (OPT-3)"; else no "status --prune" "ec=$EC out=$OUT"; fi

# ---------- Test 23 (v2.1): skeleton still works in-process ----------
echo -e "def foo():\n    pass\ndef bar():\n    pass" > _e2e_skel.py
OUT=$($VCS skeleton _e2e_skel.py 2>&1)
EC=$?
if [[ $EC -eq 0 && "$OUT" == *"def foo"* && "$OUT" == *"def bar"* ]]; then ok "skeleton in-process works (OPT-1)"; else no "skeleton in-process" "ec=$EC out=$OUT"; fi

# ---------- Test 24 (v2.1): help text mentions gc command ----------
OUT=$($VCS --help 2>&1)
if echo "$OUT" | grep -q "gc"; then ok "--help lists gc command"; else no "--help gc" "out=$OUT"; fi

# Cleanup
rm -f _e2e_*.txt _e2e_*.bin
rm -rf _e2e_dir .vcs_store.json .vcs_snapshots/
rmdir "$TEST_DIR" 2>/dev/null

echo
echo "================================================"
echo "  PASS: $PASS   FAIL: $FAIL"
echo "================================================"
if [[ $FAIL -gt 0 ]]; then exit 1; else exit 0; fi
