#!/usr/bin/env python3
"""
agy-tree — depth-limited, .gitignore-aware directory tree for AGY

Outputs a tree-style view of a directory, respecting .gitignore rules and
hiding common build/vendor directories by default. Designed to replace the
"blind list_dir hunt" AGY described in #306.

Default behavior:
  - Depth: 2 levels
  - Respects .gitignore (parses it, skips matched paths)
  - Hard-ignores: node_modules, .git, dist, build, target, .next,
    __pycache__, .cache, venv, .venv, .turbo, .parcel-cache, coverage,
    .nuxt, .svelte-kit
  - Hidden directories (those that were filtered) are listed at the bottom
    with file counts, so the agent knows they exist without seeing contents
  - Tree-style output (NOT JSON) — easier to scan, fewer tokens

Usage:
  agy-tree <path>                  # default depth 2
  agy-tree <path> --depth 3        # custom depth
  agy-tree <path> --all            # show everything (no filtering)
  agy-tree <path> --hidden-only    # show only the normally-hidden dirs

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
}

# Files we always hide by default.
HIDDEN_FILES = {
    '.DS_Store', 'Thumbs.db', '.gitignore', '.gitkeep',
}

# Source file extensions — show line counts instead of byte sizes for these.
# AGY requested line counts because view_file caps at 800 lines.
# Includes ALL text-based files where line count is meaningful, not just
# programming languages — configs, markup, data formats, etc.
# Defined at module level (Gemini review fix — avoids per-file allocation).
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


def parse_gitignore(directory: Path) -> list:
    """
    Parse .gitignore in the given directory. Returns a list of patterns.

    Only handles the most common cases: simple patterns, wildcards, and
    negation. Doesn't handle nested .gitignore files in subdirs (limitation
    we accept for v1).
    """
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
    """
    Check if a relative path matches any gitignore pattern.

    Handles two cases:
    - Basename matching for simple patterns (e.g. '*.log', 'node_modules')
    - Relative-to-root matching for patterns with slashes (e.g. 'src/*.log',
      'config/local.json')

    This is the fix for Gemini's high-priority review comment — the previous
    version only checked the basename, which broke matching for nested
    patterns like 'src/*.log'.
    """
    # For directory paths, ensure trailing slash for dir-only patterns
    path_to_match = rel_path_str + '/' if is_dir and not rel_path_str.endswith('/') else rel_path_str
    basename = path_to_match.rstrip('/').split('/')[-1]

    for pattern in patterns:
        negate = False
        if pattern.startswith('!'):
            negate = True
            pattern = pattern[1:]

        # Directory-only patterns (ending with /)
        if pattern.endswith('/'):
            if is_dir:
                # Match against basename or relative path
                if fnmatch.fnmatch(basename, pattern.rstrip('/')) or \
                   fnmatch.fnmatch(path_to_match, pattern) or \
                   fnmatch.fnmatch(path_to_match.rstrip('/'), pattern.rstrip('/')):
                    return not negate  # negate=True means un-hide
            continue

        # Patterns with slashes → match against relative path
        if '/' in pattern:
            if fnmatch.fnmatch(rel_path_str, pattern) or \
               fnmatch.fnmatch(path_to_match, pattern):
                return not negate
            continue

        # Simple patterns (no slashes) → match against basename
        if fnmatch.fnmatch(basename, pattern):
            return not negate
        # Also match against the full relative path (for nested entries)
        if fnmatch.fnmatch(rel_path_str, pattern):
            return not negate

    return False


def should_hide(name: str, rel_path_str: str, is_dir: bool, gitignore_patterns: list, show_all: bool) -> bool:
    """Decide if a file/dir should be hidden from the tree."""
    if show_all:
        return False
    # Hidden dirs (hardcoded list) — check basename
    if is_dir and name in HIDDEN_DIRS:
        return True
    # Hidden files
    if not is_dir and name in HIDDEN_FILES:
        return True
    # Gitignore — use relative path for proper matching
    if matches_gitignore(rel_path_str, is_dir, gitignore_patterns):
        return True
    return False


def count_files_recursive(path: Path, max_depth: int = 5) -> int:
    """Count files in a directory recursively (capped at max_depth for safety)."""
    count = 0
    try:
        for entry in path.iterdir():
            if entry.is_dir():
                if max_depth > 0:
                    count += count_files_recursive(entry, max_depth - 1)
                # Don't count the dir itself
            else:
                count += 1
    except (PermissionError, OSError):
        pass
    return count


def build_tree(path: Path, root_path: Path, current_depth: int, max_depth: int,
               gitignore_patterns: list, show_all: bool, prefix: str = '',
               hidden_summary: list = None) -> list:
    """
    Recursively build the tree output. Returns a list of formatted strings.

    hidden_summary is a list we append (dir_name, file_count) tuples to for
    the bottom-of-output summary of hidden directories.

    root_path is the original directory passed by the user — we compute
    relative paths from it for proper .gitignore matching (Gemini fix).
    """
    if hidden_summary is None:
        hidden_summary = []

    if current_depth > max_depth:
        return []

    try:
        entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except (PermissionError, OSError) as e:
        return [f"{prefix}[permission denied]"]

    # Filter entries
    visible = []
    for entry in entries:
        # Compute relative path from root for gitignore matching
        try:
            rel_path = entry.relative_to(root_path)
            rel_path_str = rel_path.as_posix()
        except ValueError:
            rel_path_str = entry.name

        if should_hide(entry.name, rel_path_str, entry.is_dir(), gitignore_patterns, show_all):
            if entry.is_dir():
                # Add to hidden summary
                file_count = count_files_recursive(entry)
                hidden_summary.append((entry.name, file_count))
            continue
        visible.append(entry)

    if not visible and current_depth > 1:
        # Don't show empty dirs at depth > 1 (they clutter output)
        return []

    lines = []
    for i, entry in enumerate(visible):
        is_last = (i == len(visible) - 1)
        connector = '└── ' if is_last else '├── '
        if entry.is_dir():
            # Recurse into subdirectory
            child_count = sum(1 for _ in entry.iterdir())
            lines.append(f"{prefix}{connector}{entry.name}/  ({child_count} items)")
            if current_depth < max_depth:
                # Recurse with extended prefix
                extension = '    ' if is_last else '│   '
                sub_lines = build_tree(
                    entry, root_path, current_depth + 1, max_depth,
                    gitignore_patterns, show_all,
                    prefix + extension, hidden_summary
                )
                lines.extend(sub_lines)
        else:
            # File — show name + line count (for source files) or size (for others)
            # AGY requested line counts because view_file caps at 800 lines —
            # knowing the count in advance tells AGY if it needs agy-skeleton.
            suffix = entry.suffix.lower()
            if suffix in SOURCE_EXTENSIONS:
                # Show line count for source files.
                # Read in binary chunks and count newlines — much faster than
                # decoding line-by-line, and avoids hanging on massive generated
                # files (Gemini + AGY review fix).
                # Uses `with` context manager to prevent file descriptor leaks
                # (AGY review fix).
                try:
                    with entry.open('rb') as f:
                        line_count = sum(
                            chunk.count(b'\n')
                            for chunk in iter(lambda: f.read(65536), b'')
                        )
                    if line_count > 800:
                        size_str = f"{line_count} lines ⚠"  # warning: exceeds view_file limit
                    else:
                        size_str = f"{line_count} lines"
                except OSError:
                    size_str = "? lines"
            else:
                # Show byte size for non-source files (configs, JSON, etc.)
                try:
                    size = entry.stat().st_size
                    if size < 1024:
                        size_str = f"{size} B"
                    elif size < 1024 * 1024:
                        size_str = f"{size / 1024:.1f} KB"
                    else:
                        size_str = f"{size / (1024 * 1024):.1f} MB"
                except OSError:
                    size_str = "?"
            lines.append(f"{prefix}{connector}{entry.name}  ({size_str})")

    return lines


def _dir_contains_matching_files(path: Path, filter_pattern: str, max_depth: int = 10) -> bool:
    """
    Check if a directory (recursively) contains any file matching the glob
    pattern. Used by build_tree_filtered to decide whether to show a branch.
    Capped at max_depth for safety.
    """
    import fnmatch as fnm
    try:
        for entry in path.iterdir():
            # Skip hidden dirs/files to avoid scanning node_modules/.git/etc
            # (Gemini + AGY review fix — performance + correctness)
            if entry.name in HIDDEN_DIRS or entry.name in HIDDEN_FILES:
                continue
            if entry.is_dir():
                if max_depth > 0 and _dir_contains_matching_files(entry, filter_pattern, max_depth - 1):
                    return True
            else:
                if fnm.fnmatch(entry.name, filter_pattern):
                    return True
    except (PermissionError, OSError):
        pass
    return False


def build_tree_filtered(path: Path, root_path: Path, current_depth: int, max_depth: int,
                        gitignore_patterns: list, show_all: bool, filter_pattern: str,
                        prefix: str = '', hidden_summary: list = None) -> list:
    """
    Build a filtered tree — only shows directories that contain files matching
    the filter_pattern, and the matching files themselves. Prunes branches
    with no matching files. (AGY V2 request — monorepo navigation)
    """
    import fnmatch as fnm
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
                file_count = count_files_recursive(entry)
                hidden_summary.append((entry.name, file_count))
            continue

        if entry.is_dir():
            # Only include this directory if it contains matching files
            if _dir_contains_matching_files(entry, filter_pattern):
                visible.append(entry)
        else:
            # Only include this file if it matches the filter
            if fnm.fnmatch(entry.name, filter_pattern):
                visible.append(entry)

    if not visible:
        return []

    lines = []
    for i, entry in enumerate(visible):
        is_last = (i == len(visible) - 1)
        connector = '└── ' if is_last else '├── '
        if entry.is_dir():
            try:
                child_count = sum(1 for _ in entry.iterdir())
                child_count_str = f"{child_count} items"
            except (PermissionError, OSError):
                child_count_str = "? items"
            lines.append(f"{prefix}{connector}{entry.name}/  ({child_count_str})")
            if current_depth < max_depth:
                extension = '    ' if is_last else '│   '
                sub_lines = build_tree_filtered(
                    entry, root_path, current_depth + 1, max_depth,
                    gitignore_patterns, show_all, filter_pattern,
                    prefix + extension, hidden_summary
                )
                lines.extend(sub_lines)
        else:
            # File — show name + line count/size
            suffix = entry.suffix.lower()
            if suffix in SOURCE_EXTENSIONS:
                try:
                    with entry.open('rb') as f:
                        line_count = sum(
                            chunk.count(b'\n')
                            for chunk in iter(lambda: f.read(65536), b'')
                        )
                    if line_count > 800:
                        size_str = f"{line_count} lines ⚠"
                    else:
                        size_str = f"{line_count} lines"
                except OSError:
                    size_str = "? lines"
            else:
                try:
                    size = entry.stat().st_size
                    if size < 1024:
                        size_str = f"{size} B"
                    elif size < 1024 * 1024:
                        size_str = f"{size / 1024:.1f} KB"
                    else:
                        size_str = f"{size / (1024 * 1024):.1f} MB"
                except OSError:
                    size_str = "?"
            lines.append(f"{prefix}{connector}{entry.name}  ({size_str})")

    return lines


def main():
    parser = argparse.ArgumentParser(
        description="AGY directory tree generator — depth-limited, .gitignore-aware."
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
        # Just list the hidden dirs with counts
        hidden = []
        try:
            for entry in sorted(path.iterdir(), key=lambda e: e.name.lower()):
                # Compute relative path for gitignore matching
                try:
                    rel_path_str = entry.relative_to(path).as_posix()
                except ValueError:
                    rel_path_str = entry.name
                if entry.is_dir() and should_hide(entry.name, rel_path_str, True, gitignore_patterns, False):
                    file_count = count_files_recursive(entry)
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

    # Build the tree — pass `path` as root_path so we can compute relative
    # paths for proper .gitignore matching (Gemini fix).
    hidden_summary = []

    if args.filter:
        # Filter mode: only show paths leading to files matching the glob.
        # Uses a recursive walk that prunes branches with no matching files.
        import fnmatch as fnm
        tree_lines = build_tree_filtered(
            path, root_path=path, current_depth=1, max_depth=args.depth,
            gitignore_patterns=gitignore_patterns, show_all=args.all,
            filter_pattern=args.filter, prefix='', hidden_summary=hidden_summary
        )
    else:
        tree_lines = build_tree(
            path, root_path=path, current_depth=1, max_depth=args.depth,
            gitignore_patterns=gitignore_patterns, show_all=args.all,
            hidden_summary=hidden_summary
        )

    # Header
    abs_path = path.resolve()
    visible_count = len([l for l in tree_lines if l and not l.startswith(' ') or l.strip().startswith(('├', '└'))])
    print(f"{abs_path}")
    print()

    # Tree
    for line in tree_lines:
        print(line)

    # Hidden summary at bottom
    if hidden_summary and not args.all:
        print()
        print("---")
        print("Hidden by default (use --all to show):")
        for name, count in hidden_summary:
            print(f"  {name}/  [{count} files]")

    # Footer
    print("---")
    if args.depth > 1:
        print(f"Use `agy-tree <subdir>` to expand any directory above (depth={args.depth}).")
    else:
        print(f"Use `agy-tree <subdir>` to expand any directory above.")


if __name__ == '__main__':
    main()
