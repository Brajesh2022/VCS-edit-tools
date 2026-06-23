#!/usr/bin/env bash
#
# agy-test — run tests, show only failures + summary
#
# Wraps common test runners and filters output to show only:
#   - Failing test names + file paths + line numbers
#   - Stack traces (abbreviated)
#   - Summary (X passed, Y failed, Z skipped)
#
# AGY requested this: "An agy-test wrapper that suppresses the 500 lines
# of passing tests and only prints the stack traces of the failing tests
# would be a godsend."
#
# Usage:
#   agy-test pytest                    # run pytest, show failures only
#   agy-test pytest tests/test_foo.py  # pass through args
#   agy-test jest                      # run jest
#   agy-test vitest                    # run vitest
#   agy-test cargo test                # run cargo test
#   agy-test go test ./...             # run go test
#   agy-test rspec                     # run rspec
#   agy-test npm test                  # run npm test (generic)
#   agy-test <any-command>             # run any command, filter output
#
# How it works:
#   1. Runs the command as-is, capturing full output
#   2. Saves full output to /tmp/agy-test-full.log
#   3. Filters output to show only failure-related lines
#   4. Prints filtered output + summary
#
# If all tests pass, prints a short success message.
# If no tests fail but there are warnings/errors, shows those.

set -uo pipefail

if [ $# -eq 0 ]; then
  echo "Usage: agy-test <command> [args...]"
  echo ""
  echo "Examples:"
  echo "  agy-test pytest"
  echo "  agy-test pytest tests/test_foo.py -v"
  echo "  agy-test jest"
  echo "  agy-test vitest"
  echo "  agy-test cargo test"
  echo "  agy-test go test ./..."
  echo "  agy-test npm test"
  echo ""
  echo "Full output is saved to /tmp/agy-test-full.log"
  exit 1
fi

FULL_LOG="/tmp/agy-test-full.log"

echo "Running: $*"
echo "Full output will be saved to $FULL_LOG"
echo "---"

# Run the command, capture output. Don't fail on non-zero exit (tests might fail).
OUTPUT=$("$@" 2>&1)
EXIT_CODE=$?

# Save full output
echo "$OUTPUT" > "$FULL_LOG"

# Determine the test runner from the first arg
RUNNER=$(basename "$1")

# Filter patterns that indicate failure-related lines
# These are common across test runners:
#   - "FAIL" / "FAILED" / "fail" / "failure"
#   - "Error" / "ERROR" / "Exception"
#   - "assert" (assertion errors)
#   - Stack trace lines (File "...", line N)
#   - "✕" (jest/vitest failure marker)
#   - "✗" (generic failure marker)
#   - "--- Failed" sections
#   - Summary lines (passed/failed/skipped counts)

# Run the filter based on the runner
case "$RUNNER" in
  pytest|py.test)
    # pytest: show FAILED lines + short test summary + errors
    echo "$OUTPUT" | grep -E '(FAILED|ERROR|ERRORS|short test summary|failed in|passed.*failed|failed.*passed|: ERROR|: FAILED|assert|Error|Traceback|File "|  Line|raised|Exception)' || true
    ;;
  jest)
    # jest: show ✕ lines + failure summaries
    echo "$OUTPUT" | grep -E '(✕|✗|FAIL |●|failed|passing|failing|pending|Error|expect|Received|Expected|at Object|at .*test)' || true
    ;;
  vitest)
    # vitest: same as jest
    echo "$OUTPUT" | grep -E '(✕|✗|FAIL |●|failed|passing|failing|pending|Error|expect|Received|Expected|at Object|at .*test)' || true
    ;;
  cargo)
    # cargo test: show failures + test result line
    echo "$OUTPUT" | grep -E '(test result|FAILED|test .* \.\.\. FAILED|panicked|assertion|left ==|right ==|stack backtrace|note:|error\[)' || true
    ;;
  go)
    # go test: show FAIL lines + --- FAIL sections
    echo "$OUTPUT" | grep -E '(--- FAIL|FAIL\s|ok\s|PASS|panic|goroutine|Error Trace|Error:|Test:)' || true
    ;;
  rspec)
    # rspec: show failure lines + summary
    echo "$OUTPUT" | grep -E '(Failure|FAILED|failures|pending|Finished in|examples|Expected|got|to)' || true
    ;;
  npm|npx|node)
    # generic node — show errors + failures
    echo "$OUTPUT" | grep -E '(FAIL|✕|✗|failed|Error|Exception|expect|Received|Expected|assert|at .*|passed|passing|[nN]ot [fF]ound|[nN]o such file)' || true
    ;;
  *)
    # Unknown runner — show lines matching common failure patterns
    echo "$OUTPUT" | grep -iE '(fail|error|exception|assert|traceback|panic|✕|✗|not found|No such file)' || true
    ;;
esac

# Always show the last 10 lines (usually contains the summary)
echo ""
echo "--- Last 10 lines (summary) ---"
echo "$OUTPUT" | tail -10

echo ""
echo "---"
echo "Exit code: $EXIT_CODE"
echo "Full output: $FULL_LOG ($(wc -l < "$FULL_LOG") lines)"
if [ $EXIT_CODE -eq 0 ]; then
  echo "✓ All tests passed (or no failures detected)."
else
  echo "⚠ Tests failed. See full log at $FULL_LOG for details."
fi

exit $EXIT_CODE
