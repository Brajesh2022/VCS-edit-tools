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
SKELETON:  vcs skeleton <filepath> [start-end]              (structural view)
TREE:      vcs tree [path] [--depth N] [--all]              (Prioritise over ls or list tools)
GREP:      vcs grep <query> [path] [-i]
FMT:       vcs fmt [--check] [path]
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

- For heavy code files, run `vcs skeleton <filepath>` first to get the structural overview.
- Use `vcs tree` to explore directory structure (it skips `.git`, `node_modules`, etc.).
- After a successful edit, the old blob is still valid for editing on different portion of file— but re-read the region of file if you need to make another edit to same region you just changed after that blob.
- **vcs read refuses binary files, images etc**: so for those you can try internal tools...
