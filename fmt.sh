#!/usr/bin/env bash
#
# agy-fmt — format staged files before commit
#
# Runs the appropriate formatter on git-staged files only. AGY explicitly
# requested this as a CLI command (not a hook) so it has full control over
# when formatting happens.
#
# Usage:
#   agy-fmt              # format all staged files
#   agy-fmt --check      # check formatting without modifying (dry run)
#
# Detection logic:
#   - If package.json has "prettier" → run npx prettier --write on staged .js/.jsx/.ts/.tsx/.css/.json/.md files
#   - If package.json has "eslint" → run npx eslint --fix on staged .js/.jsx/.ts/.tsx files
#   - If pyproject.toml or .ruff.toml exists → run ruff format on staged .py files
#   - If .black config exists → run black on staged .py files
#   - If go.mod exists → run gofmt on staged .go files
#   - If Cargo.toml exists → run rustfmt on staged .rs files
#
# Only runs formatters that are actually installed in the repo. If no
# formatter is detected, prints a message and exits 0 (no error).

set -euo pipefail

CHECK_MODE=false
if [ "${1:-}" = "--check" ]; then
  CHECK_MODE=true
fi

# Get staged files (only added/modified, not deleted)
STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null || echo "")

if [ -z "$STAGED_FILES" ]; then
  echo "No staged files to format."
  exit 0
fi

FORMATTED=0

# --- JavaScript/TypeScript ---
JS_FILES=$(echo "$STAGED_FILES" | grep -E '\.(js|jsx|ts|tsx|mjs|cjs)$' || echo "")
if [ -n "$JS_FILES" ]; then
  # Prettier
  if [ -f "package.json" ] && grep -q '"prettier"' package.json 2>/dev/null; then
    echo "Formatting JS/TS files with prettier..."
    if [ "$CHECK_MODE" = "true" ]; then
      echo "$JS_FILES" | xargs -d '\n' npx prettier --check 2>/dev/null || true
    else
      echo "$JS_FILES" | xargs -d '\n' npx prettier --write 2>/dev/null || true
      echo "$JS_FILES" | xargs -d '\n' git add 2>/dev/null || true
      FORMATTED=$((FORMATTED + 1))
      echo "  ✓ prettier applied + staged"
    fi
  fi
  # ESLint
  if [ -f "package.json" ] && grep -q '"eslint"' package.json 2>/dev/null; then
    echo "Linting JS/TS files with eslint --fix..."
    if [ "$CHECK_MODE" = "true" ]; then
      echo "$JS_FILES" | xargs -d '\n' npx eslint 2>/dev/null || true
    else
      echo "$JS_FILES" | xargs -d '\n' npx eslint --fix 2>/dev/null || true
      echo "$JS_FILES" | xargs -d '\n' git add 2>/dev/null || true
      FORMATTED=$((FORMATTED + 1))
      echo "  ✓ eslint --fix applied + staged"
    fi
  fi
fi

# --- Python ---
PY_FILES=$(echo "$STAGED_FILES" | grep -E '\.py$' || echo "")
if [ -n "$PY_FILES" ]; then
  # Ruff
  if [ -f "pyproject.toml" ] || [ -f ".ruff.toml" ] || [ -f "ruff.toml" ]; then
    if command -v ruff >/dev/null 2>&1; then
      echo "Formatting Python files with ruff format..."
      if [ "$CHECK_MODE" = "true" ]; then
        echo "$PY_FILES" | xargs -d '\n' ruff format --diff 2>/dev/null || true
      else
        echo "$PY_FILES" | xargs -d '\n' ruff format 2>/dev/null || true
        echo "$PY_FILES" | xargs -d '\n' ruff check --fix 2>/dev/null || true
        echo "$PY_FILES" | xargs -d '\n' git add 2>/dev/null || true
        FORMATTED=$((FORMATTED + 1))
        echo "  ✓ ruff format + check --fix applied + staged"
      fi
    fi
  # Black
  elif [ -f "pyproject.toml" ] && grep -q '\[tool.black\]' pyproject.toml 2>/dev/null; then
    if command -v black >/dev/null 2>&1; then
      echo "Formatting Python files with black..."
      if [ "$CHECK_MODE" = "true" ]; then
        echo "$PY_FILES" | xargs -d '\n' black --check 2>/dev/null || true
      else
        echo "$PY_FILES" | xargs -d '\n' black 2>/dev/null || true
        echo "$PY_FILES" | xargs -d '\n' git add 2>/dev/null || true
        FORMATTED=$((FORMATTED + 1))
        echo "  ✓ black applied + staged"
      fi
    fi
  fi
fi

# --- Go ---
GO_FILES=$(echo "$STAGED_FILES" | grep -E '\.go$' || echo "")
if [ -n "$GO_FILES" ] && [ -f "go.mod" ]; then
  if command -v gofmt >/dev/null 2>&1; then
    echo "Formatting Go files with gofmt..."
    if [ "$CHECK_MODE" = "true" ]; then
      echo "$GO_FILES" | xargs -d '\n' gofmt -l 2>/dev/null || true
    else
      echo "$GO_FILES" | xargs -d '\n' gofmt -w 2>/dev/null || true
      echo "$GO_FILES" | xargs -d '\n' git add 2>/dev/null || true
      FORMATTED=$((FORMATTED + 1))
      echo "  ✓ gofmt applied + staged"
    fi
  fi
fi

# --- Rust ---
RS_FILES=$(echo "$STAGED_FILES" | grep -E '\.rs$' || echo "")
if [ -n "$RS_FILES" ] && [ -f "Cargo.toml" ]; then
  if command -v rustfmt >/dev/null 2>&1; then
    echo "Formatting Rust files with rustfmt..."
    if [ "$CHECK_MODE" = "true" ]; then
      echo "$RS_FILES" | xargs -d '\n' rustfmt --check 2>/dev/null || true
    else
      echo "$RS_FILES" | xargs -d '\n' rustfmt 2>/dev/null || true
      echo "$RS_FILES" | xargs -d '\n' git add 2>/dev/null || true
      FORMATTED=$((FORMATTED + 1))
      echo "  ✓ rustfmt applied + staged"
    fi
  fi
fi

# --- CSS/SCSS/HTML/JSON/MD (prettier handles these too) ---
OTHER_PRETTIER_FILES=$(echo "$STAGED_FILES" | grep -E '\.(css|scss|html|json|md|yaml|yml)$' || echo "")
if [ -n "$OTHER_PRETTIER_FILES" ] && [ -f "package.json" ] && grep -q '"prettier"' package.json 2>/dev/null; then
  if [ "$CHECK_MODE" = "false" ]; then
    echo "$OTHER_PRETTIER_FILES" | xargs -d '\n' npx prettier --write 2>/dev/null || true
    echo "$OTHER_PRETTIER_FILES" | xargs -d '\n' git add 2>/dev/null || true
    FORMATTED=$((FORMATTED + 1))
    echo "  ✓ prettier applied to CSS/HTML/JSON/MD + staged"
  fi
fi

if [ $FORMATTED -eq 0 ] && [ "$CHECK_MODE" = "false" ]; then
  echo "No formatters detected for staged files. Install prettier/ruff/black/gofmt/rustfmt to enable formatting."
fi

if [ "$CHECK_MODE" = "true" ]; then
  echo "Check complete. Run without --check to apply formatting."
fi

exit 0
