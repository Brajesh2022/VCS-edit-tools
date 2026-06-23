# VCS Edit CLI & Plugins

A powerful, blob-hash-based file editing CLI specifically designed for AI agents (like Antigravity, Claude, etc.) to perform complex edits with high token efficiency.

It provides a rich set of commands for reading, replacing, inserting, and batch-editing files without the need to repeat full file contents, reducing token usage drastically while maintaining strong guarantees via 3-way-merge conflict resolution.

## Features

- **Read & Skeleton**: Read files with line numbers. Large files (>800 lines) automatically fall back to an AST-aware skeleton view (showing function signatures and collapsed bodies).
- **Edit via Blob Hashes**: Edit files using a snapshot hash, ensuring edits apply to the correct version of the file even if other changes have happened.
- **Batch Editing**: Apply multiple edits across different files atomically.
- **3-Way Merge**: Conflict resolution automatically handles cases where the underlying file has changed.
- **AI Agent Plugins**: Includes plugins for AI agents (like Antigravity) that inject system prompts instructing them on how to use this CLI efficiently.

## Installation

You can install the CLI globally on your system using the cross-platform installer script. It supports Linux, macOS, Windows (Git Bash/WSL), and Android (Termux).

```bash
curl -sSL https://raw.githubusercontent.com/Brajesh2022/vcs-cli/main/install.sh | bash
```

The installer will:
1. Clone this repository to `~/.vcs-cli`.
2. Link the `vcs` command to your local bin directory (e.g., `~/.local/bin`).
3. Prompt you to install AI agent plugins (currently supports Antigravity).

### Non-Interactive Installation (CI/CD)

If you are using this in GitHub Actions or other CI/CD environments, you can pass `-y` to skip prompts:

```bash
curl -sSL https://raw.githubusercontent.com/Brajesh2022/vcs-cli/main/install.sh | bash -s -- -y
```

To automatically install plugins as well:

```bash
curl -sSL https://raw.githubusercontent.com/Brajesh2022/vcs-cli/main/install.sh | bash -s -- -y --install-plugins
```

## Usage

```bash
# Read a file
vcs read <filepath> [start-end]

# Replace lines using heredoc
vcs replace <target> <start-end> << 'EOF'
new content
EOF

# Insert lines
vcs insert <target> <line> << 'EOF'
new line
EOF

# Batch edit multiple files
vcs batch << 'EOF'
[
  {"target":"<blob|path>","type":"replace","line_range":"8-50","content":"code"}
]
EOF

# View structural skeleton
vcs skeleton <filepath> [start-end]

# List all blob mappings
vcs status
```

*Note: `<target>` can be either a file path or a blob hash obtained from `vcs read`.*

## AI Agent Plugins

The `.agy/` directory contains the plugin for the Antigravity AI agent. When installed, it hooks into the agent's prompts to instruct it on how to use the `vcs` CLI, avoiding standard inefficient tools like `cat`, `grep`, or `sed` for complex edits.

## License

MIT License
