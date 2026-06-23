#!/usr/bin/env bash
# End-to-end smoke test for the VCS CLI wrapper.
# Drives `./vcs` as a subprocess the way an AI agent would.
set -u

VCS=/home/z/my-project/download/vcs/vcs
TEST_DIR=/home/z/my-project/download/vcs
cd "$TEST_DIR" || exit 99

PASS=0
FAIL=0
ok(){ echo "[PASS] $1"; PASS=$((PASS+1)); }
no(){ echo "[FAIL] $1 — $2"; FAIL=$((FAIL+1)); }

# Clean state
rm -rf .vcs_store.json .vcs_snapshots/ _e2e_*.txt 2>/dev/null

# ---------- Test 1: read a file → returns JSON with blob ----------
cat > _e2e_a.txt << 'PY'
def hello():
    print("hello")

def world():
    print("world")
PY

OUT=$($VCS read _e2e_a.txt)
STATUS=$(echo "$OUT" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('status','?'))")
if [[ "$STATUS" == "?" ]]; then
  # `read` doesn't include status field in our spec, check for 'blob' instead
  BLOB=$(echo "$OUT" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('blob',''))")
  if [[ -n "$BLOB" ]]; then ok "read returns JSON with blob"; else no "read" "no blob"; fi
else
  BLOB=$(echo "$OUT" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('blob',''))")
  ok "read returned (status=$STATUS, blob=$BLOB)"
fi
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

# ---------- Test 4: replace happy path ----------
echo 'REPLACED_LINE' > /tmp/vcs_new.txt
OUT=$($VCS replace "$BLOB" 2-3 /tmp/vcs_new.txt)
EC=$?
NEW_STATUS=$(echo "$OUT" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('status','?'))")
NEW_BLOB=$(echo "$OUT" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('new_blob',''))")
if [[ $EC -eq 0 && "$NEW_STATUS" == "ok" ]]; then ok "replace happy path"; else no "replace happy path" "ec=$EC status=$NEW_STATUS"; fi
echo "    new_blob=$NEW_BLOB"

# Verify file content (cat strips trailing newline, so compare with trailing newline stripped)
AFTER=$(cat _e2e_a.txt)
EXPECTED='def hello():
REPLACED_LINE
def world():
    print("world")'
if [[ "$AFTER" == "$EXPECTED" ]]; then ok "replace content correct"; else no "replace content" "got=$AFTER"; fi

# ---------- Test 5: replace with stale blob → conflict (overlap) ----------
# Modify line 1 (outside agent's edit range of 4-4) — should auto-merge
# But first, re-read to get the current blob
OUT=$($VCS read _e2e_a.txt)
CUR_BLOB=$(echo "$OUT" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('blob',''))")
# Stale the blob: edit line 1 outside of agent's view
sed -i 's/def hello():/def hello_changed():/' _e2e_a.txt
# Now try to replace line 4 using the OLD blob — should auto-merge
echo 'WORLD_REPLACED' > /tmp/vcs_new.txt
OUT=$($VCS replace "$CUR_BLOB" 4-4 /tmp/vcs_new.txt)
EC=$?
STATUS=$(echo "$OUT" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('status','?'))")
if [[ $EC -eq 0 && "$STATUS" == "auto_merged" ]]; then ok "non-overlapping conflict → auto_merged"; else no "auto-merge" "ec=$EC status=$STATUS out=$OUT"; fi

AFTER=$(cat _e2e_a.txt)
EXPECTED='def hello_changed():
REPLACED_LINE
def world():
WORLD_REPLACED'
if [[ "$AFTER" == "$EXPECTED" ]]; then ok "auto-merge content correct"; else no "auto-merge content" "got=$AFTER"; fi

# ---------- Test 6: overlapping conflict → exit 1 ----------
# Re-read
OUT=$($VCS read _e2e_a.txt)
CUR_BLOB=$(echo "$OUT" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('blob',''))")
# Modify line 4 (inside agent's edit range of 4-4) — should report conflict
sed -i '4s/.*/CONCURRENT_CHANGE/' _e2e_a.txt
echo 'AGENT_REPLACE' > /tmp/vcs_new.txt
OUT=$($VCS replace "$CUR_BLOB" 4-4 /tmp/vcs_new.txt)
EC=$?
STATUS=$(echo "$OUT" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('status','?'))")
if [[ $EC -eq 1 && "$STATUS" == "conflict" ]]; then ok "overlap conflict → exit 1"; else no "overlap conflict" "ec=$EC status=$STATUS out=$OUT"; fi
# Verify file was NOT modified by agent
AFTER=$(sed -n '4p' _e2e_a.txt)
if [[ "$AFTER" == "CONCURRENT_CHANGE" ]]; then ok "file unchanged on conflict"; else no "file unchanged" "line 4=$AFTER"; fi

# ---------- Test 7: insert command ----------
OUT=$($VCS read _e2e_a.txt)
CUR_BLOB=$(echo "$OUT" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('blob',''))")
echo 'INSERTED_LINE_1' > /tmp/vcs_ins.txt
echo 'INSERTED_LINE_2' >> /tmp/vcs_ins.txt
OUT=$($VCS insert "$CUR_BLOB" 2 /tmp/vcs_ins.txt)
EC=$?
STATUS=$(echo "$OUT" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('status','?'))")
if [[ $EC -eq 0 && "$STATUS" == "ok" ]]; then ok "insert happy path"; else no "insert" "ec=$EC status=$STATUS out=$OUT"; fi
# Verify line 2 is INSERTED_LINE_1
L2=$(sed -n '2p' _e2e_a.txt)
L3=$(sed -n '3p' _e2e_a.txt)
if [[ "$L2" == "INSERTED_LINE_1" && "$L3" == "INSERTED_LINE_2" ]]; then ok "insert content correct"; else no "insert content" "L2=$L2 L3=$L3"; fi

# ---------- Test 8: delete command ----------
OUT=$($VCS read _e2e_a.txt)
CUR_BLOB=$(echo "$OUT" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('blob',''))")
# Delete lines 2-3 (the inserted lines)
OUT=$($VCS delete "$CUR_BLOB" 2-3)
EC=$?
STATUS=$(echo "$OUT" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('status','?'))")
if [[ $EC -eq 0 && "$STATUS" == "ok" ]]; then ok "delete happy path"; else no "delete" "ec=$EC status=$STATUS out=$OUT"; fi
L2=$(sed -n '2p' _e2e_a.txt)
if [[ "$L2" != "INSERTED_LINE_1" ]]; then ok "delete content correct"; else no "delete content" "L2 still=$L2"; fi

# ---------- Test 9: read range with truncation ----------
python3 -c "
for i in range(1,1001):
    print(f'line {i}')
" > _e2e_big.txt
OUT=$($VCS read _e2e_big.txt)
TRUNC=$(echo "$OUT" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('truncated',0))")
SHOWN=$(echo "$OUT" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('shown_range',''))")
NEXT=$(echo "$OUT" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('next_command') or '')")
if [[ "$TRUNC" == "200" && "$SHOWN" == "1-800" && "$NEXT" == *"801-1000"* ]]; then ok "truncation (1000-line file)"; else no "truncation" "trunc=$TRUNC shown=$SHOWN next=$NEXT"; fi

# ---------- Test 10: short blob prefix works for replace ----------
OUT=$($VCS read _e2e_big.txt)
FULL_BLOB=$(echo "$OUT" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('blob',''))")
SHORT=${FULL_BLOB:0:8}
echo 'short_blob_test' > /tmp/vcs_new.txt
OUT=$($VCS replace "$SHORT" 1-1 /tmp/vcs_new.txt)
EC=$?
STATUS=$(echo "$OUT" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('status','?'))")
if [[ $EC -eq 0 && "$STATUS" == "ok" ]]; then ok "short blob prefix accepted"; else no "short blob" "ec=$EC status=$STATUS out=$OUT"; fi

# ---------- Test 11: status command ----------
OUT=$($VCS status)
EC=$?
HAS_BLOBS=$(echo "$OUT" | python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d.get('blobs',{})))")
if [[ $EC -eq 0 && "$HAS_BLOBS" -gt 0 ]]; then ok "status lists registered blobs"; else no "status" "ec=$EC count=$HAS_BLOBS"; fi

# ---------- Test 12: version flag ----------
OUT=$($VCS --version 2>&1)
if [[ "$OUT" == *"0.1.0"* ]]; then ok "--version works"; else no "--version" "out=$OUT"; fi

# ---------- Test 13: help flag ----------
OUT=$($VCS --help 2>&1)
if echo "$OUT" | grep -q "read" && echo "$OUT" | grep -q "replace"; then ok "--help lists commands"; else no "--help" "out=$OUT"; fi

# Cleanup
rm -f _e2e_a.txt _e2e_big.txt /tmp/vcs_new.txt /tmp/vcs_ins.txt
rm -rf .vcs_store.json .vcs_snapshots/

echo
echo "================================================"
echo "  PASS: $PASS   FAIL: $FAIL"
echo "================================================"
if [[ $FAIL -gt 0 ]]; then exit 1; else exit 0; fi
