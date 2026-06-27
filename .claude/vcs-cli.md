# Bro, please use my VCS CLI instead of your built-in tools for things like reading, writing, editing, and exploring files. I designed it specifically for you, bro. Please don't ignore it.  

## Commands (quick reference)

```
READ:      vcs read <filepath> [start-end] [--symbol <name>]
REPLACE:   vcs replace <filepath> <blob> <start-end> << 'EOF' ... EOF
INSERT:    vcs insert <filepath> <blob> <line> << 'EOF' ... EOF
DELETE:    vcs delete <filepath> [<blob> <start-end>]       (omit blob+range to delete entire file/dir)
CREATE:    vcs create <filepath> << 'EOF' ... EOF           (new file with content)
BATCH:     vcs batch << 'EOF'  === REPLACE <filepath> <blob> <start-end> ===\nnew content... (repeat blocks for more files)  EOF
DIFF:      vcs diff <filepath> <blob>
SKELETON:  vcs skeleton <filepath> [start-end]              (Returns Code's structural skeleton)
LIST:      vcs list [path] [--depth N] [--all]              (Prioritise over ls or list tools)
GREP:      vcs grep <query> [path] [-i]                     (Outputs blob hash for instant edits)
FMT:       vcs fmt [--check] [path]                        (auto-format code syntax)
TEST:      vcs test <cmd> [path]
STATUS:    vcs status [--prune]                          (list blobs, or prune stale entries)

```

## Critical Rules

**1. Never auto-calculate lines.**
Use exact line numbers from what you already read. No guessing or recalculating positions after edits.

**2. Both filepath AND blob required for edits.**

**3. Re-read rule.**
- Editing a region untouched since your last read → reuse the old blob, no re-read needed.
- Editing a region you already modified → re-read that portion first to get a fresh blob.

**4. On conflict → re-read and retry.**

## Workflow Tips

- For heavy code files, run `vcs skeleton <filepath>` first to get the structural overview (supports most file types including Markdown).
- Use `vcs list` to explore directory structure (it skips `.git`, `node_modules`, etc.).
- Use `vcs grep` to search. It outputs the file's blob hash with the match, letting you instantly run `vcs replace` or `vcs insert` without needing an intermediate `vcs read`!
