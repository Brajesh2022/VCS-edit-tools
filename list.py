#!/usr/bin/env python3
"""
vcs tree — depth-limited, .gitignore-aware directory tree for the VCS CLI.

Outputs a tree-style view of a directory, respecting .gitignore rules and
hiding common build/vendor directories by default.

Default behavior:
  - Depth: 2 levels
  - Respects .gitignore (parses it, skips matched paths)
  - Hard-ignores: node_modules, .git, dist, build, target, .next,
    __pycache__, .cache, venv, .venv, .turbo, .parcel-cache, coverage,
    .nuxt, .svelte-kit
  - Hidden directories (those that were filtered) are listed at the bottom
    with file counts, so the agent knows they exist without seeing contents.
  - Tree-style output (NOT JSON) — easier to scan, fewer tokens

Performance optimization (v2):
  - Directories with 10+ items are NOT recursed into — we just show a summary
    like `Frontend/ (2 dirs, 8 files)`. This prevents the tree command from
    spending huge time walking through heavy directories (.git, node_modules
    even when not in HIDDEN_DIRS, vendor folders, generated folders, etc.).
  - Hidden directory file counts use a capped, breadth-first counter to avoid
    recursing into massive sub-trees.
  - All filesystem walks skip .git-style directories early to avoid stat storms.

Usage:
  vcs tree <path>                  # default depth 2
  vcs tree <path> --depth 3        # custom depth
  vcs tree <path> --all            # show everything (no filtering)
  vcs tree <path> --hidden-only    # show only the normally-hidden dirs

Exit codes:
  0 = success
  1 = path not found / not a directory
"""

import os
import sys
import argparse
import fnmatch
from pathlib import Path


# Directories we always hide by default (unless --all is passed).
# These are universally heavy build/vendor/dependency dirs that almost never
# contain code the agent needs to read directly.
HIDDEN_DIRS = {
    'node_modules', '.git', 'dist', 'build', 'target', '.next',
    '__pycache__', '.cache', 'venv', '.venv', '.turbo', '.parcel-cache',
    'coverage', '.nuxt', '.svelte-kit', '.idea', '.vscode',
    'vendor',  # Go/PHP vendor dirs
    '.terraform',
    '.gradle',
    '.maven',
    'Pods',  # iOS CocoaPods
    '.vcs_snapshots',  # VCS-internal snapshot store
}

# Files we always hide by default.
HIDDEN_FILES = {
    '.DS_Store', 'Thumbs.db', '.gitignore', '.gitkeep',
    '.vcs_store.json',  # VCS-internal registry
}

# Source file extensions — show line counts instead of byte sizes for these.
# Includes ALL text-based files where line count is meaningful, not just
# programming languages — configs, markup, data formats, etc.
SOURCE_EXTENSIONS = {
    # Programming languages
    '.py', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs',
    '.go', '.rs', '.java', '.c', '.cpp', '.h', '.hpp',
    '.rb', '.php', '.swift', '.kt', '.scala', '.clj',
    '.sh', '.bash', '.zsh', '.sql',
    # Markup & docs
    '.md', '.html', '.htm', '.xml', '.svg',
    # Config & data
    '.json', '.yml', '.yaml', '.toml', '.ini', '.cfg',
    '.csv', '.tsv',
    # Styles
    '.css', '.scss', '.sass', '.less',
    # Component frameworks
    '.vue', '.svelte',
}

# v2 performance: if a directory has this many direct entries (or more),
# don't recurse into it — just show a summary line. This is the cap the
# user asked for ("if any directory files or subfolder touch mark of 10
# then don't try to Fetch further").
DIR_ITEM_CAP = 10

# v2 performance: hidden directories often contain thousands of files
# (node_modules, .git). Cap the recursive file-count walk so we don't hang.
HIDDEN_DIR_COUNT_CAP = 200


def parse_gitignore(directory: Path) -> list:
    """Parse .gitignore in the given directory. Returns a list of patterns."""
    gitignore = directory / '.gitignore'
    if not gitignore.exists():
        return []

    patterns = []
    for line in gitignore.read_text(errors='replace').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        patterns.append(line)
    return patterns


def matches_gitignore(rel_path_str: str, is_dir: bool, patterns: list) -> bool:
    """Check if a relative path matches any gitignore pattern."""
    path_to_match = rel_path_str + '/' if is_dir and not rel_path_str.endswith('/') else rel_path_str
    basename = path_to_match.rstrip('/').split('/')[-1]

    for pattern in patterns:
        negate = False
        if pattern.startswith('!'):
            negate = True
            pattern = pattern[1:]

        if pattern.endswith('/'):
            if is_dir:
                if fnmatch.fnmatch(basename, pattern.rstrip('/')) or \
                   fnmatch.fnmatch(path_to_match, pattern) or \
                   fnmatch.fnmatch(path_to_match.rstrip('/'), pattern.rstrip('/')):
                    return not negate
            continue

        if '/' in pattern:
            if fnmatch.fnmatch(rel_path_str, pattern) or \
               fnmatch.fnmatch(path_to_match, pattern):
                return not negate
            continue

        if fnmatch.fnmatch(basename, pattern):
            return not negate
        if fnmatch.fnmatch(rel_path_str, pattern):
            return not negate

    return False


def should_hide(name: str, rel_path_str: str, is_dir: bool, gitignore_patterns: list, show_all: bool) -> bool:
    """Decide if a file/dir should be hidden from the tree."""
    if show_all:
        return False
    if is_dir and name in HIDDEN_DIRS:
        return True
    if not is_dir and name in HIDDEN_FILES:
        return True
    if matches_gitignore(rel_path_str, is_dir, gitignore_patterns):
        return True
    return False


def count_files_capped(path: Path, cap: int = HIDDEN_DIR_COUNT_CAP) -> int:
    """Count files in a directory recursively, stopping early once `cap` is hit.

    Used for the hidden-directory summary at the bottom of the output.
    Skips other hidden directories to avoid walking node_modules inside
    node_modules etc.

    Uses breadth-first traversal (deque.popleft) so we hit the cap on the
    shallow, broad parts of the tree first — gives a more representative
    count when the cap kicks in. Also refuses to follow symlinks to prevent
    infinite loops on symlink cycles (e.g. `ln -s .. self_loop`).
    """
    from collections import deque
    count = 0
    queue = deque([path])
    while queue:
        current = queue.popleft()
        try:
            for entry in current.iterdir():
                if count >= cap:
                    return count  # fast exit
                # Skip symlinks to prevent infinite loops on cycles
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    # Don't descend into nested hidden dirs
                    if entry.name in HIDDEN_DIRS:
                        continue
                    queue.append(entry)
                else:
                    count += 1
        except (PermissionError, OSError):
            pass
    return count


def _count_direct_children(path: Path) -> tuple[int, int]:
    """Return (num_dirs, num_files) of direct children of `path`.

    Used for the summary line `Frontend/ (2 dirs, 8 files)`.
    """
    n_dirs = 0
    n_files = 0
    try:
        for entry in path.iterdir():
            if entry.is_dir():
                n_dirs += 1
            else:
                n_files += 1
    except (PermissionError, OSError):
        pass
    return n_dirs, n_files


def _format_dir_summary(n_dirs: int, n_files: int) -> str:
    """Format a directory summary line like `Frontend/ (2 dirs, 8 files)`."""
    parts = []
    if n_dirs == 1:
        parts.append("1 dir")
    elif n_dirs != 0:
        parts.append(f"{n_dirs} dirs")
    if n_files == 1:
        parts.append("1 file")
    elif n_files != 0:
        parts.append(f"{n_files} files")
    if not parts:
        return "empty"
    return ", ".join(parts)


def _format_file_suffix(entry: Path) -> str:
    """Return the suffix string for a file entry, e.g. `(45 lines)` or `(2.3 KB)`."""
    suffix = entry.suffix.lower()
    if suffix in SOURCE_EXTENSIONS:
        try:
            with entry.open('rb') as f:
                line_count = sum(
                    chunk.count(b'\n')
                    for chunk in iter(lambda: f.read(65536), b'')
                )
            if line_count > 800:
                return f"{line_count} lines ⚠"
            return f"{line_count} lines"
        except OSError:
            return "? lines"
    else:
        try:
            size = entry.stat().st_size
            if size < 1024:
                return f"{size} B"
            elif size < 1024 * 1024:
                return f"{size / 1024:.1f} KB"
            else:
                return f"{size / (1024 * 1024):.1f} MB"
        except OSError:
            return "?"


def build_list(path: Path, root_path: Path, current_depth: int, max_depth: int,
               gitignore_patterns: list, show_all: bool, prefix: str = '',
               hidden_summary: list = None) -> list:
    """Recursively build the list output. Returns a list of formatted strings."""
    if hidden_summary is None:
        hidden_summary = []

    if current_depth > max_depth:
        return []

    try:
        entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except (PermissionError, OSError):
        return [f"{prefix}[permission denied]"]

    # Filter entries
    visible = []
    for entry in entries:
        try:
            rel_path = entry.relative_to(root_path)
            rel_path_str = rel_path.as_posix()
        except ValueError:
            rel_path_str = entry.name

        if should_hide(entry.name, rel_path_str, entry.is_dir(), gitignore_patterns, show_all):
            if entry.is_dir():
                file_count = count_files_capped(entry)
                hidden_summary.append((entry.name, file_count))
            continue
        visible.append(entry)

    if not visible and current_depth > 1:
        return []

    lines = []
    for entry in visible:
        if entry.is_dir():
            n_dirs, n_files = _count_direct_children(entry)
            summary = _format_dir_summary(n_dirs, n_files)
            lines.append(f"{prefix}{entry.name}/  ({summary})")

            child_too_many = (n_dirs + n_files) >= DIR_ITEM_CAP
            if current_depth < max_depth and not child_too_many:
                extension = '  '
                sub_lines = build_list(
                    entry, root_path, current_depth + 1, max_depth,
                    gitignore_patterns, show_all,
                    prefix + extension, hidden_summary
                )
                lines.extend(sub_lines)
            elif child_too_many and current_depth < max_depth:
                lines.append(f"{prefix}  … (many items)")
        else:
            size_str = _format_file_suffix(entry)
            lines.append(f"{prefix}{entry.name}  ({size_str})")

    return lines


def build_list_filtered(path: Path, root_path: Path, current_depth: int, max_depth: int,
                        gitignore_patterns: list, show_all: bool, filter_pattern: str,
                        prefix: str = '', hidden_summary: list = None) -> list:
    """Build a filtered list — only shows directories that contain files matching
    the filter_pattern, and the matching files themselves.
    """
    if hidden_summary is None:
        hidden_summary = []

    if current_depth > max_depth:
        return []

    try:
        entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except (PermissionError, OSError):
        return [f"{prefix}[permission denied]"]

    visible = []
    for entry in entries:
        try:
            rel_path = entry.relative_to(root_path)
            rel_path_str = rel_path.as_posix()
        except ValueError:
            rel_path_str = entry.name

        if should_hide(entry.name, rel_path_str, entry.is_dir(), gitignore_patterns, show_all):
            if entry.is_dir():
                file_count = count_files_capped(entry)
                hidden_summary.append((entry.name, file_count))
            continue

        if entry.is_dir():
            if _dir_contains_matching_files(entry, filter_pattern):
                visible.append(entry)
        else:
            if fnmatch.fnmatch(entry.name, filter_pattern):
                visible.append(entry)

    if not visible:
        return []

    lines = []
    for entry in visible:
        if entry.is_dir():
            n_dirs, n_files = _count_direct_children(entry)
            summary = _format_dir_summary(n_dirs, n_files)
            lines.append(f"{prefix}{entry.name}/  ({summary})")
            
            child_too_many = (n_dirs + n_files) >= DIR_ITEM_CAP
            if current_depth < max_depth and not child_too_many:
                extension = '  '
                sub_lines = build_list_filtered(
                    entry, root_path, current_depth + 1, max_depth,
                    gitignore_patterns, show_all, filter_pattern,
                    prefix + extension, hidden_summary
                )
                lines.extend(sub_lines)
            elif child_too_many and current_depth < max_depth:
                lines.append(f"{prefix}  … (many items)")
        else:
            size_str = _format_file_suffix(entry)
            lines.append(f"{prefix}{entry.name}  ({size_str})")

    return lines
def _dir_contains_matching_files(path: Path, filter_pattern: str, max_depth: int = 10) -> bool:
    """Check if a directory (recursively) contains any file matching the glob."""
    try:
        for entry in path.iterdir():
            if entry.name in HIDDEN_DIRS or entry.name in HIDDEN_FILES:
                continue
            if entry.is_dir():
                if max_depth > 0 and _dir_contains_matching_files(entry, filter_pattern, max_depth - 1):
                    return True
            else:
                if fnmatch.fnmatch(entry.name, filter_pattern):
                    return True
    except (PermissionError, OSError):
        pass
    return False


def main():
    parser = argparse.ArgumentParser(
        description="vcs tree — depth-limited, .gitignore-aware directory tree."
    )
    parser.add_argument('path', nargs='?', default='.', help='Directory to tree')
    parser.add_argument('--depth', type=int, default=2,
                        help='Maximum depth (default: 2)')
    parser.add_argument('--all', action='store_true',
                        help='Show everything (no filtering, no hiding)')
    parser.add_argument('--hidden-only', action='store_true',
                        help='Show only the normally-hidden directories (with counts)')
    parser.add_argument('--filter', type=str, default=None,
                        help='Glob filter — only show paths leading to matching files (e.g. "*.jsx")')
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"Error: path not found: {path}", file=sys.stderr)
        sys.exit(1)
    if not path.is_dir():
        print(f"Error: not a directory: {path}", file=sys.stderr)
        sys.exit(1)

    gitignore_patterns = parse_gitignore(path) if not args.all else []

    if args.hidden_only:
        hidden = []
        try:
            for entry in sorted(path.iterdir(), key=lambda e: e.name.lower()):
                try:
                    rel_path_str = entry.relative_to(path).as_posix()
                except ValueError:
                    rel_path_str = entry.name
                if entry.is_dir() and should_hide(entry.name, rel_path_str, True, gitignore_patterns, False):
                    file_count = count_files_capped(entry)
                    hidden.append(f"{entry.name}/  [{file_count} files]")
        except (PermissionError, OSError):
            pass
        if hidden:
            print(f"Hidden directories in {path}:")
            for h in hidden:
                print(f"  {h}")
        else:
            print(f"No hidden directories in {path}.")
        return

    hidden_summary = []

    if args.filter:
        list_lines = build_list_filtered(
            path, root_path=path, current_depth=1, max_depth=args.depth,
            gitignore_patterns=gitignore_patterns, show_all=args.all,
            filter_pattern=args.filter, prefix='', hidden_summary=hidden_summary
        )
    else:
        list_lines = build_list(
            path, root_path=path, current_depth=1, max_depth=args.depth,
            gitignore_patterns=gitignore_patterns, show_all=args.all,
            hidden_summary=hidden_summary
        )

    # Header
    abs_path = path.resolve()
    print(f"{abs_path}")
    print()

    # List
    for line in list_lines:
        print(line)

    # Hidden summary at bottom
    if hidden_summary and not args.all:
        print()
        print("---")
        print("Hidden by default (use --all to show):")
        for name, count in hidden_summary:
            print(f"  {name}/  [{count} files]")

    # Footer — uses `vcs list` (was previously `agy-tree`)
    print("---")
    if args.depth > 1:
        print(f"Use `vcs tree <subdir>` to expand any directory above (depth={args.depth}).")
    else:
        print(f"Use `vcs tree <subdir>` to expand any directory above.")


if __name__ == '__main__':
    main()
