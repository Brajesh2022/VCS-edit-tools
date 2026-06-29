#!/usr/bin/env python3
"""
agy-skeleton — AST-aware file skeleton generator for AGY

Outputs a file's structure (imports + function/class signatures + collapsed
bodies) in the EXACT same format as Antigravity CLI's view_file tool, so the
agent doesn't have to context-switch.

Output format matches view_file:
  File Path: `file:///abs/path`
  Total Lines: N
  Total Bytes: M
  Showing skeleton (collapsed function bodies). Use view_file with StartLine/EndLine to see full bodies.
  1: <first line>
  2: <second line>
  ...
  N: <last line>

Body collapse format: `// ... N lines ...` (for JS/TS) or `# ... N lines ...`
(for Python). The line number shown is the FIRST line of the collapsed block
(e.g. if a function body starts on line 50 and runs to line 80, the skeleton
shows line 50 with the marker — line 51-80 are not shown).

Comments and imports are always preserved (AGY said it relies on comments
for intent).

Supported file types:
  - .py: parsed via Python's ast module (accurate)
  - .js/.jsx/.ts/.tsx/.mjs/.cjs: regex-based (function/class signatures)
  - Other: fallback (first 50 + last 50 lines, middle collapsed)

Usage:
  agy-skeleton <path>
  agy-skeleton <path> --json    # machine-readable (for debugging)

Exit codes:
  0 = success
  1 = file not found / not a regular file
  2 = parse error (Python syntax error in .py file)
"""

import os
import sys
import json
import argparse
from pathlib import Path


# === Python AST-based skeletonization ===

def _process_node(node, lines, show_lines, is_class_body=False):
    """
    Recursively process an AST node, marking which lines should be shown.

    For FunctionDef: show signature + docstring, collapse body.
    For ClassDef: show signature + docstring, then RECURSE into body to
                  show method signatures (don't collapse the whole class).
    For other nodes: show the whole thing.
    """
    import ast

    # Show decorator lines
    if hasattr(node, 'decorator_list'):
        for dec in node.decorator_list:
            if hasattr(dec, 'lineno'):
                show_lines.add(dec.lineno)

    # Show the node's own line (def/class/import/etc.)
    if hasattr(node, 'lineno'):
        show_lines.add(node.lineno)

    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        # Function: show signature lines (def line through to first body line),
        # show docstring, collapse the rest of the body.
        if hasattr(node, 'body') and node.body:
            first_body_line = node.body[0].lineno
            # Show all lines from def line up to (but not including) first body line
            for ln in range(node.lineno, first_body_line):
                show_lines.add(ln)
            # Show docstring if present (first statement is a string literal)
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                show_lines.add(first.lineno)
                if hasattr(first, 'end_lineno') and first.end_lineno:
                    for ln in range(first.lineno, first.end_lineno + 1):
                        show_lines.add(ln)
        # Body is collapsed (don't recurse into it)

    elif isinstance(node, ast.ClassDef):
        # Class: show signature lines + docstring, then RECURSE into body
        # to show method signatures. This is the key fix — without recursion,
        # entire classes get collapsed, hiding all methods.
        if hasattr(node, 'body') and node.body:
            first_body_line = node.body[0].lineno
            # Show all lines from class line up to (but not including) first body line
            for ln in range(node.lineno, first_body_line):
                show_lines.add(ln)
            # Show docstring if present
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                show_lines.add(first.lineno)
                if hasattr(first, 'end_lineno') and first.end_lineno:
                    for ln in range(first.lineno, first.end_lineno + 1):
                        show_lines.add(ln)
            # Recurse into class body — show method signatures, class-level
            # assignments, nested classes, etc.
            for child in node.body:
                _process_node(child, lines, show_lines, is_class_body=True)

    else:
        # Non-def/class node (import, assignment, etc.): show the whole thing
        if hasattr(node, 'end_lineno') and node.end_lineno:
            for ln in range(node.lineno, node.end_lineno + 1):
                show_lines.add(ln)


def skeletonize_python(path: Path) -> tuple:
    """
    Parse a Python file with the ast module and return a tuple of
    (skeleton_lines, total_lines, total_bytes) where skeleton_lines is a list
    of (line_no, text) tuples.

    Bodies of functions are collapsed to a single `# ... N lines ...` marker.
    Class bodies are RECURSED into so method signatures are preserved (only
    method bodies are collapsed).

    Returns (skeleton, total_lines, total_bytes).
    """
    import ast

    source = path.read_text(encoding='utf-8', errors='replace')
    tree = ast.parse(source)

    lines = source.splitlines()
    total_lines = len(lines)

    # Build a set of line numbers that should be SHOWN.
    show_lines = set()

    # Process each top-level node
    for node in tree.body:
        _process_node(node, lines, show_lines)

    # Also show comments and blank lines (AGY relies on comments for intent)
    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith('#'):
            show_lines.add(i)
        # Blank lines — always show them (they're cheap and preserve formatting)
        if not stripped:
            show_lines.add(i)

    # Now build the skeleton output: walk through lines, showing each visible
    # line, and inserting a collapse marker when we hit a hidden block.
    result = []
    i = 1
    while i <= len(lines):
        if i in show_lines:
            result.append((i, lines[i - 1]))
            i += 1
        else:
            # Count consecutive hidden lines
            hidden_start = i
            while i <= len(lines) and i not in show_lines:
                i += 1
            hidden_count = i - hidden_start
            # Insert the collapse marker at the hidden_start line number.
            # Preserve the indentation of the first hidden line so the marker
            # aligns with the surrounding code (Gemini review suggestion).
            first_hidden = lines[hidden_start - 1] if hidden_start <= len(lines) else ""
            indent = ""
            for ch in first_hidden:
                if ch in ' \t':
                    indent += ch
                else:
                    break
            result.append((hidden_start, f"{indent}# ... {hidden_count} lines ..."))

    return result, total_lines, path.stat().st_size


# === JS/TS regex-based skeletonizat# Patterns that match the START of a function/class/etc.
REGEX_PATTERNS = {
    'js_ts': [
        r'^\s*(export\s+)?(default\s+)?(async\s+)?function\s+\w+',
        r'^\s*(export\s+)?(const|let|var)\s+\w+\s*=\s*(\([^)]*\)|\w+)\s*=>',
        r'^\s*(export\s+)?(default\s+)?(abstract\s+)?class\s+\w+',
        r'^\s+(public|private|protected|static|async|get|set|\s)*\w+\s*\([^)]*\)\s*({|=>)',
        r'^\s*(export\s+)?(interface|type)\s+\w+',
        r'^\s*(export\s+)?(const|let|var)\s+\w+\s*:\s*React\.',
    ],
    'rust': [
        r'^\s*(pub\s+)?fn\s+\w+',
        r'^\s*(pub\s+)?impl\s+\w+',
        r'^\s*(pub\s+)?struct\s+\w+',
        r'^\s*(pub\s+)?enum\s+\w+',
        r'^\s*(pub\s+)?trait\s+\w+',
    ],
    'go': [
        r'^\s*func\s+\w+',
        r'^\s*func\s+\([^)]+\)\s+\w+',
        r'^\s*type\s+\w+',
    ],
    'java_kt_cs': [
        r'^\s*(public|private|protected|internal)?\s*(static|final|abstract|sealed)?\s*(class|interface|record|enum|object|struct)\s+\w+',
        r'^\s*(public|private|protected|internal)?\s*(static|final|abstract|override|virtual)?\s*\w+(?:<[^>]+>)?\s+\w+\s*\(',
        r'^\s*(fun)\s+\w+', # Kotlin function
    ],
    'ruby': [
        r'^\s*def\s+\w+',
        r'^\s*class\s+\w+',
        r'^\s*module\s+\w+',
    ],
}

def skeletonize_regex(path: Path, patterns: list[str]) -> tuple:
    """
    Regex-based skeleton for supported languages.
    """
    import re

    source = path.read_text(encoding='utf-8', errors='replace')
    lines = source.splitlines()
    total_lines = len(lines)
    signature_regexes = [re.compile(p) for p in patterns]

    show_lines = set()

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        # Imports
        if stripped.startswith('import ') or stripped.startswith('export ') or stripped.startswith('use ') or stripped.startswith('package '):
            show_lines.add(i)
            if not stripped.endswith(';') and not stripped.endswith('from'):
                j = i + 1
                while j <= len(lines) and ';' not in lines[j - 1]:
                    show_lines.add(j)
                    j += 1
                    if j - i > 20: break
                if j <= len(lines): show_lines.add(j)
            continue
        # Comments
        if stripped.startswith('//') or stripped.startswith('/*') or stripped.startswith('*') or stripped.startswith('#'):
            show_lines.add(i)
            continue
        # Blank lines
        if not stripped:
            show_lines.add(i)
            continue
        # Signatures
        for regex in signature_regexes:
            if regex.match(line):
                show_lines.add(i)
                break

    result = []
    i = 1
    while i <= len(lines):
        if i in show_lines:
            result.append((i, lines[i - 1]))
            i += 1
        else:
            hidden_start = i
            while i <= len(lines) and i not in show_lines:
                i += 1
            hidden_count = i - hidden_start
            first_hidden = lines[hidden_start - 1] if hidden_start <= len(lines) else ""
            indent = ""
            for ch in first_hidden:
                if ch in ' \t': indent += ch
                else: break
            marker = "#" if path.suffix == '.rb' else "//"
            result.append((hidden_start, f"{indent}{marker} ... {hidden_count} lines ..."))

    return result, total_lines, path.stat().st_size


# === Markdown skeletonizer ===

def skeletonize_markdown(path: Path) -> tuple:
    """
    Markdown skeletonizer — preserves headings, code fence markers, and
    first lines of each section. Collapses long paragraphs, code blocks,
    and lists.

    Uses HTML comment syntax for collapse markers: <!-- ... N lines ... -->
    """
    source = path.read_text(encoding='utf-8', errors='replace')
    lines = source.splitlines()
    total_lines = len(lines)

    show_lines = set()
    in_code_block = False
    lines_since_heading = -1

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()

        # Track code fence state
        if stripped.startswith('```') or stripped.startswith('~~~'):
            show_lines.add(i)
            in_code_block = not in_code_block
            continue

        if in_code_block:
            continue

        # Headings (always show)
        if stripped.startswith('#'):
            show_lines.add(i)
            lines_since_heading = 0
            continue

        # First 2 non-empty lines after a heading (give context).
        # Uses a state variable instead of checking the previous line,
        # which correctly handles blank lines between heading and content
        # (Gemini + AGY review fix — both flagged the same bug).
        if lines_since_heading >= 0 and stripped:
            show_lines.add(i)
            lines_since_heading += 1
            if lines_since_heading >= 2:
                lines_since_heading = -1
            continue

        # Blank lines (preserve structure)
        if not stripped:
            show_lines.add(i)
            continue

        # Horizontal rules (---, ***, ___)
        if stripped in ('---', '***', '___'):
            show_lines.add(i)
            continue

    # Build the skeleton
    result = []
    i = 1
    while i <= len(lines):
        if i in show_lines:
            result.append((i, lines[i - 1]))
            i += 1
        else:
            hidden_start = i
            while i <= len(lines) and i not in show_lines:
                i += 1
            hidden_count = i - hidden_start
            # Preserve indentation
            first_hidden = lines[hidden_start - 1] if hidden_start <= len(lines) else ""
            indent = ""
            for ch in first_hidden:
                if ch in ' \t':
                    indent += ch
                else:
                    break
            result.append((hidden_start, f"{indent}<!-- ... {hidden_count} lines ... -->"))

    return result, total_lines, path.stat().st_size


# === JSON skeletonizer ===

def skeletonize_json(path: Path) -> tuple:
    """
    JSON skeletonizer — shows structural keys and collapses array elements.
    Lines with key-value pairs (containing `":` ) are shown; array values
    and nested objects are collapsed.
    """
    source = path.read_text(encoding='utf-8', errors='replace')
    lines = source.splitlines()
    total_lines = len(lines)

    show_lines = set()

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        # Show lines with keys (containing ":")
        if '":' in stripped or '"' in stripped and ':' in stripped:
            show_lines.add(i)
        # Show structural braces
        if stripped in ('{', '}', '[', ']', '{,', '},', '[,', '],'):
            show_lines.add(i)
        # Show first and last 5 lines always
        if i <= 5 or i > total_lines - 5:
            show_lines.add(i)

    result = []
    i = 1
    while i <= len(lines):
        if i in show_lines:
            result.append((i, lines[i - 1]))
            i += 1
        else:
            hidden_start = i
            while i <= len(lines) and i not in show_lines:
                i += 1
            hidden_count = i - hidden_start
            first_hidden = lines[hidden_start - 1] if hidden_start <= len(lines) else ""
            indent = ""
            for ch in first_hidden:
                if ch in ' \t':
                    indent += ch
                else:
                    break
            result.append((hidden_start, f"{indent}// ... {hidden_count} lines ..."))

    return result, total_lines, path.stat().st_size


# === YAML skeletonizer ===

def skeletonize_yaml(path: Path) -> tuple:
    """
    YAML skeletonizer — shows keys (lines with `key:`), section markers,
    and comments. Collapses multi-line values and list items.
    """
    import re

    source = path.read_text(encoding='utf-8', errors='replace')
    lines = source.splitlines()
    total_lines = len(lines)

    show_lines = set()
    key_pattern = re.compile(r'^(?:"[^"]+"|\'[^\']+\'|[a-zA-Z_][\w\-]*)\s*:')

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        # Comments
        if stripped.startswith('#'):
            show_lines.add(i)
            continue
        # Document markers
        if stripped == '---' or stripped == '...':
            show_lines.add(i)
            continue
        # Keys (indented or not)
        if key_pattern.match(line.lstrip()):
            show_lines.add(i)
            continue
        # List item starts (show first item per group, collapse rest)
        if stripped.startswith('- '):
            show_lines.add(i)
            continue
        # Blank lines
        if not stripped:
            show_lines.add(i)
            continue

    result = []
    i = 1
    while i <= len(lines):
        if i in show_lines:
            result.append((i, lines[i - 1]))
            i += 1
        else:
            hidden_start = i
            while i <= len(lines) and i not in show_lines:
                i += 1
            hidden_count = i - hidden_start
            first_hidden = lines[hidden_start - 1] if hidden_start <= len(lines) else ""
            indent = ""
            for ch in first_hidden:
                if ch in ' \t':
                    indent += ch
                else:
                    break
            result.append((hidden_start, f"{indent}# ... {hidden_count} lines ..."))

    return result, total_lines, path.stat().st_size


# === Markup skeletonizer (HTML, XML, Vue, Svelte) ===

def skeletonize_markup(path: Path) -> tuple:
    """
    Markup skeletonizer for HTML, XML, Vue, Svelte.
    Shows opening tags, closing tags, and section markers.
    Collapses text content between tags.
    """
    import re
    source = path.read_text(encoding='utf-8', errors='replace')
    lines = source.splitlines()
    total_lines = len(lines)

    show_lines = set()

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        # Tags (opening, closing, self-closing)
        if stripped.startswith('<') or stripped.endswith('>'):
            show_lines.add(i)
            continue
        # Vue/Svelte script/style/template markers
        if stripped.startswith('<script') or stripped.startswith('<style') or stripped.startswith('<template'):
            show_lines.add(i)
            continue
        # Comments
        if stripped.startswith('<!--') or stripped.startswith('/*') or stripped.startswith('*'):
            show_lines.add(i)
            continue
        # Blank lines
        if not stripped:
            show_lines.add(i)
            continue
        # CSS selectors inside <style> blocks (lines ending with {)
        if stripped.endswith('{') or stripped == '}':
            show_lines.add(i)
            continue
        # JS inside <script> blocks (function/class/const/let/var lines)
        if re.match(r'^\s*(export\s+)?(async\s+)?(function|class|const|let|var)\s+\w+', stripped):
            show_lines.add(i)
            continue

    result = []
    i = 1
    while i <= len(lines):
        if i in show_lines:
            result.append((i, lines[i - 1]))
            i += 1
        else:
            hidden_start = i
            while i <= len(lines) and i not in show_lines:
                i += 1
            hidden_count = i - hidden_start
            first_hidden = lines[hidden_start - 1] if hidden_start <= len(lines) else ""
            indent = ""
            for ch in first_hidden:
                if ch in ' \t':
                    indent += ch
                else:
                    break
            result.append((hidden_start, f"{indent}<!-- ... {hidden_count} lines ... -->"))

    return result, total_lines, path.stat().st_size


# === CSS skeletonizer ===

def skeletonize_css(path: Path) -> tuple:
    """
    CSS/SCSS skeletonizer — shows selectors and property names.
    Collapses property values and long rule bodies.
    """
    source = path.read_text(encoding='utf-8', errors='replace')
    lines = source.splitlines()
    total_lines = len(lines)

    show_lines = set()

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        # Selectors (lines ending with {)
        if stripped.endswith('{') or stripped.endswith('},') or stripped == '}':
            show_lines.add(i)
            continue
        # At-rules (@media, @import, @keyframes, etc.)
        if stripped.startswith('@'):
            show_lines.add(i)
            continue
        # Comments
        if stripped.startswith('/*') or stripped.startswith('//') or stripped.startswith('*'):
            show_lines.add(i)
            continue
        # Blank lines
        if not stripped:
            show_lines.add(i)
            continue

    result = []
    i = 1
    while i <= len(lines):
        if i in show_lines:
            result.append((i, lines[i - 1]))
            i += 1
        else:
            hidden_start = i
            while i <= len(lines) and i not in show_lines:
                i += 1
            hidden_count = i - hidden_start
            first_hidden = lines[hidden_start - 1] if hidden_start <= len(lines) else ""
            indent = ""
            for ch in first_hidden:
                if ch in ' \t':
                    indent += ch
                else:
                    break
            result.append((hidden_start, f"{indent}/* ... {hidden_count} lines ... */"))

    return result, total_lines, path.stat().st_size


# === TOML skeletonizer ===

def skeletonize_toml(path: Path) -> tuple:
    """
    TOML skeletonizer — shows section headers [section] and key-value pairs.
    Collapses comments and multi-line values.
    """
    import re

    source = path.read_text(encoding='utf-8', errors='replace')
    lines = source.splitlines()
    total_lines = len(lines)

    show_lines = set()
    key_pattern = re.compile(r'^[a-zA-Z_][\w\-]*\s*=')

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        # Section headers
        if stripped.startswith('['):
            show_lines.add(i)
            continue
        # Key-value pairs
        if key_pattern.match(stripped):
            show_lines.add(i)
            continue
        # Comments
        if stripped.startswith('#'):
            show_lines.add(i)
            continue
        # Blank lines
        if not stripped:
            show_lines.add(i)
            continue

    result = []
    i = 1
    while i <= len(lines):
        if i in show_lines:
            result.append((i, lines[i - 1]))
            i += 1
        else:
            hidden_start = i
            while i <= len(lines) and i not in show_lines:
                i += 1
            hidden_count = i - hidden_start
            first_hidden = lines[hidden_start - 1] if hidden_start <= len(lines) else ""
            indent = ""
            for ch in first_hidden:
                if ch in ' \t':
                    indent += ch
                else:
                    break
            result.append((hidden_start, f"{indent}# ... {hidden_count} lines ..."))

    return result, total_lines, path.stat().st_size


# === CSV/TSV skeletonizer ===

def skeletonize_csv(path: Path) -> tuple:
    """
    CSV/TSV skeletonizer — shows the header row + first 5 data rows.
    Collapses the rest with a row count.
    """
    source = path.read_text(encoding='utf-8', errors='replace')
    lines = source.splitlines()
    total_lines = len(lines)

    # Show header + first 5 data rows + last 2 rows
    show_count = min(7, total_lines)  # header + 5 data + buffer
    result = []

    for i in range(min(show_count, total_lines)):
        result.append((i + 1, lines[i]))

    if total_lines > show_count + 2:
        remaining = total_lines - show_count - 2
        result.append((show_count + 1, f"# ... {remaining} more rows ..."))
        # Show last 2 rows
        for i in range(total_lines - 2, total_lines):
            result.append((i + 1, lines[i]))
    elif total_lines > show_count:
        # Show remaining rows
        for i in range(show_count, total_lines):
            result.append((i + 1, lines[i]))

    return result, total_lines, path.stat().st_size


# === Fallback for unknown file types ===

def skeletonize_fallback(path: Path) -> tuple:
    """
    For files we don't know how to parse (e.g. .txt, .log) or files >10MB
    (OOM protection): show the first 50 lines + last 50 lines, collapse
    the middle.

    Uses STREAMING (line-by-line reading with a deque for the last 50 lines)
    instead of loading the entire file into memory. This is critical for
    OOM protection — a 2GB log file would crash the runner if we used
    path.read_text(). (AGY review fix)

    Returns (skeleton, total_lines, total_bytes).
    """
    import collections

    total_bytes = path.stat().st_size
    first_50 = []
    last_50 = collections.deque(maxlen=50)
    total_lines = 0

    with path.open('r', encoding='utf-8', errors='replace') as f:
        for line in f:
            total_lines += 1
            line_clean = line.rstrip('\n')
            if total_lines <= 50:
                first_50.append((total_lines, line_clean))
            else:
                last_50.append((total_lines, line_clean))

    if total_lines <= 100:
        return first_50 + list(last_50), total_lines, total_bytes

    hidden_count = total_lines - 100
    result = first_50 + [(51, f"// ... {hidden_count} lines truncated (use view_file with StartLine/EndLine to see) ...")] + list(last_50)

    return result, total_lines, total_bytes


# === Output formatter (matches view_file format) ===

def format_skeleton_output(path: Path, skeleton: list, total_lines: int, total_bytes: int) -> str:
    """Format the skeleton list as a string matching view_file's output format."""
    abs_path = path.resolve()
    file_uri = f"file://{abs_path}"

    header = (
        f"File Path: `{file_uri}`\n"
        f"Total Lines: {total_lines}\n"
        f"Total Bytes: {total_bytes}\n"
        f"Showing skeleton (collapsed function bodies). Use view_file with StartLine/EndLine to see full bodies.\n"
    )

    body_lines = []
    for line_no, text in skeleton:
        body_lines.append(f"{line_no}: {text}")

    body = "\n".join(body_lines)
    footer = f"\nShowing skeleton of {total_lines} lines (collapsed bodies marked with '// ... N lines ...')."

    return f"{header}\n{body}\n{footer}\n"


def generate_skeleton(path: Path, start: int = None, end: int = None) -> dict:
    """Generate a skeleton view for a file. Reusable in-process entry point.

    This is the non-CLI version of main() — it doesn't print, doesn't exit,
    doesn't parse argv. Returns a dict that callers (cmd_skeleton, the >800-line
    auto-fallback in cmd_read) can use directly.

    Args:
        path:  Path to the file to skeletonize.
        start: Optional 1-indexed start line filter (None = no filter).
        end:   Optional 1-indexed end line filter (None = no filter).

    Returns:
        {
          "path": "<abs path>",
          "total_lines": N,
          "total_bytes": M,
          "range": {"start": start, "end": end},
          "skeleton_entries": total,
          "truncated": bool,
          "output": "<formatted string>",
        }

    Raises:
        FileNotFoundError: path doesn't exist
        IsADirectoryError: path is a directory
        ValueError: file looks binary (NUL bytes in first 8KB)
    """
    if not path.exists():
        raise FileNotFoundError(f"file not found: {path}")
    if not path.is_file():
        raise IsADirectoryError(f"not a regular file: {path}")

    # Binary detection (same heuristic as main())
    try:
        with path.open('rb') as f:
            chunk = f.read(8192)
        if b'\x00' in chunk:
            raise ValueError(
                f"file appears to be binary (contains null bytes in first 8KB). "
                f"skeleton only works on text files."
            )
    except OSError:
        pass  # let the skeletonizer try and fail naturally

    MAX_SKELETONIZE_SIZE = 10 * 1024 * 1024  # 10 MB
    file_size = path.stat().st_size
    suffix = path.suffix.lower()

    try:
        if file_size > MAX_SKELETONIZE_SIZE:
            skeleton, total_lines, total_bytes = skeletonize_fallback(path)
        elif suffix == '.py':
            skeleton, total_lines, total_bytes = skeletonize_python(path)
        elif suffix in ('.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs'):
            skeleton, total_lines, total_bytes = skeletonize_regex(path, REGEX_PATTERNS['js_ts'])
        elif suffix == '.rs':
            skeleton, total_lines, total_bytes = skeletonize_regex(path, REGEX_PATTERNS['rust'])
        elif suffix == '.go':
            skeleton, total_lines, total_bytes = skeletonize_regex(path, REGEX_PATTERNS['go'])
        elif suffix in ('.java', '.kt', '.cs'):
            skeleton, total_lines, total_bytes = skeletonize_regex(path, REGEX_PATTERNS['java_kt_cs'])
        elif suffix == '.rb':
            skeleton, total_lines, total_bytes = skeletonize_regex(path, REGEX_PATTERNS['ruby'])
        elif suffix == '.md':
            skeleton, total_lines, total_bytes = skeletonize_markdown(path)
        elif suffix == '.json':
            skeleton, total_lines, total_bytes = skeletonize_json(path)
        elif suffix in ('.yml', '.yaml'):
            skeleton, total_lines, total_bytes = skeletonize_yaml(path)
        elif suffix in ('.html', '.htm', '.xml', '.vue', '.svelte', '.svg'):
            skeleton, total_lines, total_bytes = skeletonize_markup(path)
        elif suffix in ('.css', '.scss', '.sass', '.less'):
            skeleton, total_lines, total_bytes = skeletonize_css(path)
        elif suffix == '.toml':
            skeleton, total_lines, total_bytes = skeletonize_toml(path)
        elif suffix in ('.csv', '.tsv'):
            skeleton, total_lines, total_bytes = skeletonize_csv(path)
        else:
            skeleton, total_lines, total_bytes = skeletonize_fallback(path)
    except SyntaxError:
        skeleton, total_lines, total_bytes = skeletonize_fallback(path)
    except Exception:
        skeleton, total_lines, total_bytes = skeletonize_fallback(path)

    # Apply --start/--end range filter
    if start is not None or end is not None:
        s = start if start is not None else 1
        e = end if end is not None else total_lines
        skeleton = [(ln, txt) for ln, txt in skeleton if s <= ln <= e]

    # Pagination cap (matches view_file's 800-line limit)
    SKELETON_ENTRY_CAP = 800
    skeleton_truncated = False
    total_skeleton_entries = len(skeleton)
    if total_skeleton_entries > SKELETON_ENTRY_CAP:
        skeleton = skeleton[:SKELETON_ENTRY_CAP]
        skeleton_truncated = True

    output_str = format_skeleton_output(path, skeleton, total_lines, total_bytes)
    if skeleton_truncated:
        last_line_shown = skeleton[-1][0] if skeleton else 0
        output_str += (
            f"\n[Showing first {SKELETON_ENTRY_CAP} of {total_skeleton_entries} skeleton entries. "
            f"Run: vcs skeleton \"{path}\" --start {last_line_shown + 1} to see more.]"
        )

    return {
        "path": str(path.resolve()),
        "total_lines": total_lines,
        "total_bytes": total_bytes,
        "range": {"start": start, "end": end},
        "skeleton_entries": total_skeleton_entries,
        "truncated": skeleton_truncated,
        "output": output_str,
    }


# === Main ===

def main():
    parser = argparse.ArgumentParser(
        description="AGY file skeleton generator — outputs a file's structure "
                    "in view_file-compatible format with function bodies collapsed."
    )
    parser.add_argument('path', help='Path to the file to skeletonize')
    parser.add_argument('--json', action='store_true',
                        help='Output as JSON (for debugging)')
    parser.add_argument('--start', type=int, default=None,
                        help='Only show skeleton entries from this line number onwards (1-indexed)')
    parser.add_argument('--end', type=int, default=None,
                        help='Only show skeleton entries up to this line number (1-indexed)')
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)
    if not path.is_file():
        print(f"Error: not a regular file: {path}", file=sys.stderr)
        sys.exit(1)

    # Content-based binary detection — if the first 8KB contains a null
    # byte, it's binary (same heuristic git uses). Prevents garbage output
    # when AGY runs agy-skeleton directly on an image/PDF/archive.
    try:
        with path.open('rb') as f:
            chunk = f.read(8192)
        if b'\x00' in chunk:
            print(
                "Error: file appears to be binary (contains null bytes in first 8KB).\n"
                "agy-skeleton only works on text files. Use view_file directly —\n"
                "Antigravity handles images, PDFs, and other binary files natively.",
                file=sys.stderr
            )
            sys.exit(1)
    except OSError:
        pass  # If we can't read it, let the skeletonizer try and fail naturally

    # File size guard — if the file is >10MB, fall back to the generic
    # skeletonizer which can handle it more gracefully. The type-specific
    # skeletonizers all read the entire file into memory, which would OOM
    # on massive files (2GB CSV logs, etc.). (AGY review fix)
    MAX_SKELETONIZE_SIZE = 10 * 1024 * 1024  # 10 MB
    file_size = path.stat().st_size

    # Pick skeletonizer based on extension. All skeletonizers return
    # (skeleton, total_lines, total_bytes) to avoid redundant disk reads.
    # If file is >10MB, always use fallback (OOM protection).
    suffix = path.suffix.lower()
    try:
        if file_size > MAX_SKELETONIZE_SIZE:
            print(f"Warning: file is {file_size / (1024*1024):.1f} MB (>10 MB limit). Using fallback skeletonizer.", file=sys.stderr)
            skeleton, total_lines, total_bytes = skeletonize_fallback(path)
        elif suffix == '.py':
            skeleton, total_lines, total_bytes = skeletonize_python(path)
        elif suffix in ('.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs'):
            skeleton, total_lines, total_bytes = skeletonize_jsts(path)
        elif suffix == '.md':
            skeleton, total_lines, total_bytes = skeletonize_markdown(path)
        elif suffix == '.json':
            skeleton, total_lines, total_bytes = skeletonize_json(path)
        elif suffix in ('.yml', '.yaml'):
            skeleton, total_lines, total_bytes = skeletonize_yaml(path)
        elif suffix in ('.html', '.htm', '.xml', '.vue', '.svelte', '.svg'):
            skeleton, total_lines, total_bytes = skeletonize_markup(path)
        elif suffix in ('.css', '.scss', '.sass', '.less'):
            skeleton, total_lines, total_bytes = skeletonize_css(path)
        elif suffix == '.toml':
            skeleton, total_lines, total_bytes = skeletonize_toml(path)
        elif suffix in ('.csv', '.tsv'):
            skeleton, total_lines, total_bytes = skeletonize_csv(path)
        else:
            skeleton, total_lines, total_bytes = skeletonize_fallback(path)
    except SyntaxError as e:
        # Python file with syntax errors — fall back to line-based view
        print(f"Warning: parse error in {path}: {e}. Falling back to line-based view.", file=sys.stderr)
        skeleton, total_lines, total_bytes = skeletonize_fallback(path)
    except Exception as e:
        print(f"Warning: error skeletonizing {path}: {e}. Falling back.", file=sys.stderr)
        skeleton, total_lines, total_bytes = skeletonize_fallback(path)

    # Apply --start/--end range filter if provided (AGY V1.5 request —
    # lets the agent skeletonize just a chunk of a massive file)
    if args.start is not None or args.end is not None:
        start = args.start if args.start is not None else 1
        end = args.end if args.end is not None else total_lines
        skeleton = [(ln, txt) for ln, txt in skeleton if start <= ln <= end]

    # Skeleton pagination — if the skeleton itself has >800 entries, truncate
    # to the first 800 and tell AGY to use --start to see more.
    # This mirrors view_file's own 800-line cap. Without this, a 10000-line
    # file with 200 functions could produce a 3000-entry skeleton that floods
    # AGY's context window (user's observation).
    SKELETON_ENTRY_CAP = 800
    skeleton_truncated = False
    total_skeleton_entries = len(skeleton)
    if total_skeleton_entries > SKELETON_ENTRY_CAP and args.json is False:
        skeleton = skeleton[:SKELETON_ENTRY_CAP]
        skeleton_truncated = True

    if args.json:
        output_str = format_skeleton_output(path, skeleton, total_lines, total_bytes)
        if skeleton_truncated:
            last_line_shown = skeleton[-1][0] if skeleton else 0
            output_str += (f"\n[Showing first {SKELETON_ENTRY_CAP} of {total_skeleton_entries} skeleton entries. "
                           f"Run: agy-skeleton \"{path}\" --start {last_line_shown + 1} to see more.]")
        print(json.dumps({
            'path': str(path.resolve()),
            'total_lines': total_lines,
            'total_bytes': total_bytes,
            'range': {'start': args.start, 'end': args.end},
            'skeleton_entries': total_skeleton_entries,
            'truncated': skeleton_truncated,
            'skeleton': [{'line': ln, 'text': txt} for ln, txt in skeleton],
            'output': output_str
        }, indent=2))
    else:
        output = format_skeleton_output(path, skeleton, total_lines, total_bytes)
        if skeleton_truncated:
            # Insert pagination note after the header line
            last_line_shown = skeleton[-1][0] if skeleton else 0
            pagination_note = (
                f"\n[Showing first {SKELETON_ENTRY_CAP} of {total_skeleton_entries} skeleton entries. "
                f"Run: agy-skeleton \"{path}\" --start {last_line_shown + 1} to see more.]"
            )
            output = output + pagination_note
        print(output)

if __name__ == "__main__":
    main()
