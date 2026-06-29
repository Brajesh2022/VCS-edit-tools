import re
from pathlib import Path

# Add this to skeleton.py
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
            # Ruby uses # for comments, others use //
            marker = "#" if path.suffix == '.rb' else "//"
            result.append((hidden_start, f"{indent}{marker} ... {hidden_count} lines ..."))

    return result, total_lines, path.stat().st_size
