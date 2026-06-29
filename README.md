# VCS Edit CLI & Agent Integrations

A powerful, blob-hash-based file editing CLI specifically designed for AI agents (Antigravity, Claude, Codex, etc.) to perform complex edits with high token efficiency.

It provides a rich set of commands for reading, replacing, inserting, creating, deleting, and batch-editing files without the need to repeat full file contents, reducing token usage drastically while maintaining strong guarantees via 3-way-merge conflict resolution.

## Features

- **Read & Skeleton**: Read files with line numbers. Large files (>800 lines) automatically fall back to an AST-aware skeleton view (showing function signatures and collapsed bodies).
- **Edit via Blob Hashes**: Edit files using a snapshot hash, ensuring edits apply to the correct version of the file even if other changes have happened. **Both filepath AND blob are required** for every edit — blob proves you read it, filepath confirms which file.
- **Create & Delete Files/Dirs**: `vcs create` makes a new file with content; `vcs delete <path>` removes a file or an entire directory tree.
- **Batch Editing**: Apply multiple edits across different files atomically. Each edit must include BOTH filepath AND blob.
- **3-Way Merge**: Conflict resolution automatically handles cases where the underlying file has changed.
- **Fast Tree**: `vcs tree` skips heavy directories (`.git`, `node_modules`, etc.) and caps at 10 items per directory — fast even on huge repos.
- **Agent Integrations**: Works with Antigravity (`.agy` plugin), Claude (`~/.claude/rules/vcs-cli.md`), and Codex (`~/.codex/AGENTS.md`).

## Installation

You can install the CLI globally on your system using the cross-platform installer script. It supports Linux, macOS, Windows (Git Bash/WSL), and Android (Termux).

```bash
curl -sSL https://raw.githubusercontent.com/Brajesh2022/VCS-edit-tools/main/install.sh | bash
```

The installer will:
1. Check for required dependencies (`git`, `python3`) and safely auto-install them via `pkg`/`apt`/`brew` if missing.
2. Clone this repository to `~/.VCS-edit-tools`.
3. Link the `vcs` command to your local bin directory (e.g., `~/.local/bin`).
4. Provide an interactive menu to install AI agent integrations:
   - **Antigravity** — `.agy` plugin (uses Gemini hooks system)
   - **Claude** — drops `vcs-cli.md` into `~/.claude/rules/` (no hooks, no plugins — uses Claude Code's rules system)
   - **Codex** — manages `~/.codex/AGENTS.md` with a safe override hierarchy

### Non-Interactive Installation (CI/CD)

If you are using this in GitHub Actions or other CI/CD environments, you can pass `-y` to skip prompts. If dependencies are missing in `-y` mode, the script will safely fail.

```bash
curl -sSL https://raw.githubusercontent.com/Brajesh2022/VCS-edit-tools/main/install.sh | bash -s -- -y
```

To automatically install specific integrations, use the `--plugins` flag (accepts a comma-separated list like `antigravity`, `claude`, `codex`, or `all`):

```bash
curl -sSL https://raw.githubusercontent.com/Brajesh2022/VCS-edit-tools/main/install.sh | bash -s -- -y --plugins claude,codex
```

## Usage

```bash
# Read a file
vcs read <filepath> [start-end]

# Replace lines using heredoc — BOTH filepath AND blob required
vcs replace <filepath> <blob> <start-end> << 'EOF'
new content
EOF

# Insert lines
vcs insert <filepath> <blob> <line> << 'EOF'
new line
EOF

# Delete a line range
vcs delete <filepath> <blob> <start-end>

# Delete a file or an entire directory
vcs delete <filepath>

# Create a new file with content
vcs create <filepath> << 'EOF'
file content
EOF

# Batch edit multiple files — BOTH filepath AND blob per edit
vcs batch << 'EOF'
[
  {"filepath":"src/auth.py","blob":"a3f9c1d2","type":"replace","line_range":"8-50","content":"code"}
]
EOF

# View structural skeleton
vcs skeleton <filepath> [start-end]

# List all blob mappings
vcs status
```

### Critical Rules (mirror what's in the agent payloads)

1. **Never auto-calculate lines.** Use exact line numbers from what you already read.
2. **Both filepath AND blob required** for `replace`, `insert`, `delete` (line-range), and `batch`. Not just one — both.
3. **Re-read rule**: editing an untouched region → reuse the old blob. Editing a region you already modified → re-read that portion first to get a fresh blob.
4. **Batch requires blob too.** No blob in any batch edit → entire batch is rejected.
5. **Replace / Insert / Batch output** is just `status: ok` — nothing else.
6. **Conflict response** is just: `Merge conflict detected. Please read the latest version and try again.` — no diff, no conflicting lines, no technical details.

### v2.1 additions

- **`vcs read` refuses binary files** (NUL bytes or >30% non-text control bytes) with a clean error instead of dumping garbled bytes. UTF-8 with multibyte chars (中文, café, emoji) is NOT falsely flagged.
- **Specific blob-mismatch errors**: `vcs replace` (and friends) now distinguish three cases that all previously surfaced as the generic "Merge conflict detected":
  - `blob 'XX' was never issued by vcs read` → you forgot to read the file first
  - `blob 'XX' was issued for '<other_file>', not for '<target>'` → wrong blob for this file
  - `Merge conflict detected...` → genuine concurrent modification (re-read & retry)
- **Bounded registry growth**: the `.vcs_store.json` registry now caps at **100 blob entries per file** (oldest pruned first), and short-prefix + full-hash duplicates are consolidated. Prevents the unbounded growth seen in v2.0.
- **`vcs gc` command** + **`vcs status --prune` flag**: garbage-collect stale registry entries (deleted files) and orphan snapshot files in one shot.
- **Skeleton is now in-process** (no subprocess): ~40% faster (`vcs skeleton` dropped from ~190ms to ~110ms).
- **`vcs read` adds a trailing newline for display** when the file has none — terminal output no longer merges the last line with the shell prompt. The on-disk file is NOT modified.

## AI Agent Integrations

### Antigravity (`.agy/`)

A standard plugin with `payload.json` + `hooks.json` that hooks into Gemini's `PreInvocation` to inject VCS instructions. Installed to `~/.gemini/config/plugins/vcs-edit/`.

### Claude (`.claude/vcs-cli.md`)

A single Markdown rules file. The installer copies it to `~/.claude/rules/vcs-cli.md`. Claude Code automatically loads rules files as part of its system prompt — no hooks, no plugins, no `settings.json` modifications. The installer also cleans up any legacy v1 hooks/plugins from previous installs.

### Codex (`.codex/AGENTS.md`)

Codex doesn't have a hooks/rules system, so it depends on `~/.codex/AGENTS.md`. The installer uses a safe hierarchy:

1. If `~/.codex/AGENTS.override.md` exists → **do nothing** (the user has explicitly overridden).
2. Else if `~/.codex/AGENTS.md` exists → **do nothing** (the user already has one).
3. Else → **create** `~/.codex/AGENTS.md` with VCS instructions.

This means the installer never clobbers a user's existing setup.

## License

MIT License
