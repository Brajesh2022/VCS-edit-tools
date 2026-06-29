import re
from pathlib import Path

# Patterns that indicate the start of a function/class/method.
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

def find_enclosing_scope(filepath: str, match_line: int) -> tuple:
    """
    Scan backwards from match_line to find the most recent function/class
    signature. Returns (line_number, signature_text) or (None, None) if
    not found.
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

    scan_start = min(match_line - 1, len(lines) - 1)
    scan_end = max(0, scan_start - 200)

    for i in range(scan_start, scan_end - 1, -1):
        line = lines[i]
        for pattern in patterns:
            if pattern.match(line):
                return i + 1, line.rstrip()

    return None, None
