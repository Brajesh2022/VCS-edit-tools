#!/usr/bin/env python3
"""
agy-grep — grep with function/class context

Wraps grep/ripgrep and prints the enclosing function or class signature
above each match (similar to `git grep -p`). Gives AGY instant context on
WHERE a match lives without needing to open the file.

Usage:
  agy-grep <pattern> <path>           # search for pattern, show context
  agy-grep <pattern> <path> -i        # case-insensitive
  agy-grep <pattern> <path> -r        # regex mode
  agy-grep <pattern> <path> --name-only  # just show file:line, no context

Output format:
  src/App.jsx:142: function handleSubmit(e) {    ← enclosing function
  src/App.jsx:145:   handleSubmit(e.target.value)  ← the actual match
  ---
  src/components/Form.jsx:88: const handleSubmit = (data) => {  ← enclosing function
  src/components/Form.jsx:92:   await api.handleSubmit(data)     ← the actual match

Supported languages for context detection:
  .py  — def, class, async def (via line scanning)
  .js/.jsx/.ts/.tsx — function, const X =, class, export default
  .go  — func, type
  .rs  — fn, impl, struct, enum
  .java/.kt — public/private/protected method/class
  .rb  — def, class
  .php — function, class

For unsupported file types, just shows the match without context.
"""

import os
import sys
import re
import argparse
import subprocess
from pathlib import Path


# Patterns that indicate the start of a function/class/method.
# Each entry: (compiled_regex, description)
# We scan backwards from the match line to find the most recent signature.
SIGNATURE_PATTERNS = {
    '.py': [
        re.compile(r'^(\s*)(async\s+)?def\s+(\w+)'),
        re.compile(r'^(\s*)class\s+(\w+)'),
    ],
    '.js': [
        re.compile(r'^(\s*)(export\s+)?(default\s+)?(async\s+)?function\s+(\w+)'),
        re.compile(r'^(\s*)(export\s+)?(const|let|var)\s+(\w+)\s*=\s*(\([^)]*\)|\w+)\s*=>'),
        re.compile(r'^(\s*)(export\s+)?(default\s+)?(abstract\s+)?class\s+(\w+)'),
        re.compile(r'^(\s+)(\w+)\s*\([^)]*\)\s*\{'),
    ],
    '.jsx': [
        re.compile(r'^(\s*)(export\s+)?(default\s+)?(async\s+)?function\s+(\w+)'),
        re.compile(r'^(\s*)(export\s+)?(const|let|var)\s+(\w+)\s*=\s*(\([^)]*\)|\w+)\s*=>'),
        re.compile(r'^(\s*)(export\s+)?(default\s+)?(abstract\s+)?class\s+(\w+)'),
        re.compile(r'^(\s+)(\w+)\s*\([^)]*\)\s*\{'),
    ],
    '.ts': [
        re.compile(r'^(\s*)(export\s+)?(default\s+)?(async\s+)?function\s+(\w+)'),
        re.compile(r'^(\s*)(export\s+)?(const|let|var)\s+(\w+)\s*=\s*(\([^)]*\)|\w+)\s*=>'),
        re.compile(r'^(\s*)(export\s+)?(default\s+)?(abstract\s+)?class\s+(\w+)'),
        re.compile(r'^(\s+)(\w+)\s*\([^)]*\)\s*[\{:]'),
        re.compile(r'^(\s*)(export\s+)?(interface|type)\s+(\w+)'),
    ],
    '.tsx': [
        re.compile(r'^(\s*)(export\s+)?(default\s+)?(async\s+)?function\s+(\w+)'),
        re.compile(r'^(\s*)(export\s+)?(const|let|var)\s+(\w+)\s*=\s*(\([^)]*\)|\w+)\s*=>'),
        re.compile(r'^(\s*)(export\s+)?(default\s+)?(abstract\s+)?class\s+(\w+)'),
        re.compile(r'^(\s+)(\w+)\s*\([^)]*\)\s*[\{:]'),
    ],
    '.go': [
        re.compile(r'^(\s*)func\s+(\w+)'),
        re.compile(r'^(\s*)func\s+\([^)]+\)\s+(\w+)'),
        re.compile(r'^(\s*)type\s+(\w+)'),
    ],
    '.rs': [
        re.compile(r'^(\s*)(pub\s+)?fn\s+(\w+)'),
        re.compile(r'^(\s*)(pub\s+)?impl\s+(\w+)'),
        re.compile(r'^(\s*)(pub\s+)?struct\s+(\w+)'),
        re.compile(r'^(\s*)(pub\s+)?enum\s+(\w+)'),
    ],
    '.java': [
        re.compile(r'^(\s*)(public|private|protected)\s+(static\s+)?(\w+)\s+(\w+)\s*\([^)]*\)\s*\{'),
        re.compile(r'^(\s*)(public|private|protected)\s+(static\s+)?(class|interface)\s+(\w+)'),
    ],
    '.kt': [
        re.compile(r'^(\s*)(fun)\s+(\w+)'),
        re.compile(r'^(\s*)(class|interface|object)\s+(\w+)'),
    ],
    '.rb': [
        re.compile(r'^(\s*)def\s+(\w+)'),
        re.compile(r'^(\s*)(class|module)\s+(\w+)'),
    ],
    '.php': [
        re.compile(r'^(\s*)(public|private|protected|static)?\s*function\s+(\w+)'),
        re.compile(r'^(\s*)(class|interface|trait)\s+(\w+)'),
    ],
    '.mjs': [
        re.compile(r'^(\s*)(export\s+)?(default\s+)?(async\s+)?function\s+(\w+)'),
        re.compile(r'^(\s*)(export\s+)?(const|let|var)\s+(\w+)\s*=\s*(\([^)]*\)|\w+)\s*=>'),
    ],
    '.cjs': [
        re.compile(r'^(\s*)(export\s+)?(default\s+)?(async\s+)?function\s+(\w+)'),
        re.compile(r'^(\s*)(export\s+)?(const|let|var)\s+(\w+)\s*=\s*(\([^)]*\)|\w+)\s*=>'),
    ],
    '.c': [
        re.compile(r'^(\s*)(\w+)\s+(\w+)\s*\([^)]*\)\s*\{'),
    ],
    '.cpp': [
        re.compile(r'^(\s*)(\w+)::(\w+)\s*\([^)]*\)\s*\{'),
        re.compile(r'^(\s*)(class|struct)\s+(\w+)'),
    ],
    '.sh': [
        re.compile(r'^(\w+)\s*\(\)\s*\{'),
        re.compile(r'^function\s+(\w+)'),
    ],
    '.bash': [
        re.compile(r'^(\w+)\s*\(\)\s*\{'),
        re.compile(r'^function\s+(\w+)'),
    ],
}


def find_enclosing_signature(filepath: str, match_line: int) -> tuple:
    """
    Scan backwards from match_line to find the most recent function/class
    signature. Returns (line_number, signature_text) or (None, None) if
    not found.

    Only scans up to 200 lines back (safety limit).
    """
    suffix = Path(filepath).suffix.lower()
    patterns = SIGNATURE_PATTERNS.get(suffix)
    if not patterns:
        return None, None

    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except OSError:
        return None, None

    # Scan backwards from the match line (0-indexed = match_line - 1)
    scan_start = min(match_line - 1, len(lines) - 1)
    scan_end = max(0, scan_start - 200)  # Don't scan more than 200 lines back

    for i in range(scan_start, scan_end - 1, -1):
        line = lines[i]
        for pattern in patterns:
            if pattern.match(line):
                return i + 1, line.rstrip()  # 1-indexed

    return None, None


def run_grep(pattern: str, path: str, case_insensitive: bool, regex_mode: bool, name_only: bool) -> list:
    """
    Run grep/ripgrep and return list of (filepath, line_number, line_text).
    Uses ripgrep (rg) if available, falls back to grep.
    """
    cmd = []

    if os.system('command -v rg >/dev/null 2>&1') == 0:
        cmd = ['rg', '--line-number', '--no-heading', '--with-filename', '--color=never']
        if case_insensitive:
            cmd.append('-i')
        if not regex_mode:
            cmd.append('--fixed-strings')
        if name_only:
            cmd = ['rg', '--files-with-matches', '--color=never']
            if case_insensitive:
                cmd.append('-i')
            if not regex_mode:
                cmd.append('--fixed-strings')
        cmd.extend(['--', pattern, path])
    else:
        cmd = ['grep', '-rn', '-H', '--color=never']
        if case_insensitive:
            cmd.append('-i')
        if not regex_mode:
            cmd.append('--fixed-strings')
        if name_only:
            cmd = ['grep', '-rl', '--color=never']
            if case_insensitive:
                cmd.append('-i')
            if not regex_mode:
                cmd.append('-F')
        cmd.extend(['--', pattern, path])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        print("Error: grep timed out after 30 seconds.", file=sys.stderr)
        sys.exit(1)

    matches = []
    for line in result.stdout.strip().split('\n'):
        if not line:
            continue
        if name_only:
            # --name-only output is just filenames (no colons)
            matches.append((line, 0, ''))
            continue
        # Parse file:line:content format using regex to handle colons in
        # filenames robustly (Gemini + AGY review fix)
        match = re.match(r'^(.+?):(\d+):(.*)$', line)
        if match:
            filepath, line_num, content = match.groups()
            matches.append((filepath, int(line_num), content))

    return matches


def main():
    parser = argparse.ArgumentParser(
        description="AGY grep with function context — shows the enclosing "
                    "function/class signature above each match."
    )
    parser.add_argument('pattern', help='Search pattern')
    parser.add_argument('path', help='File or directory to search')
    parser.add_argument('-i', '--ignore-case', action='store_true',
                        help='Case-insensitive search')
    parser.add_argument('-r', '--regex', action='store_true',
                        help='Treat pattern as regex (default: literal string)')
    parser.add_argument('--name-only', action='store_true',
                        help='Show only file paths, no line content or context')
    args = parser.parse_args()

    if not os.path.exists(args.path):
        print(f"Error: path not found: {args.path}", file=sys.stderr)
        sys.exit(1)

    matches = run_grep(args.pattern, args.path, args.ignore_case, args.regex, args.name_only)

    if not matches:
        print(f"No matches found for '{args.pattern}' in {args.path}", file=sys.stderr)
        sys.exit(0)

    if args.name_only:
        seen = set()
        for filepath, _, _ in matches:
            if filepath not in seen:
                print(filepath)
                seen.add(filepath)
        return

    # Group matches by file for cleaner output
    current_file = None
    last_sig_line = None  # Track last shown signature to avoid repetition

    for filepath, line_no, line_text in matches:
        # File separator
        if current_file != filepath:
            if current_file is not None:
                print()  # Blank line between files
            current_file = filepath
            last_sig_line = None

        # Find enclosing function/class signature
        sig_line, sig_text = find_enclosing_signature(filepath, line_no)

        # Show signature if found and different from the last one shown
        if sig_line and sig_line != last_sig_line:
            print(f"{filepath}:{sig_line}: {sig_text}    ← enclosing function")
            last_sig_line = sig_line
        elif sig_line and sig_line == last_sig_line:
            pass  # Same function, don't repeat the signature
        else:
            # No signature found (might be top-level code or unsupported file type)
            pass

        # Show the actual match
        print(f"{filepath}:{line_no}: {line_text}")

    # Summary
    print(f"\n{len(matches)} match{'es' if len(matches) != 1 else ''} in {len(set(m[0] for m in matches))} file(s).", file=sys.stderr)


if __name__ == '__main__':
    main()
