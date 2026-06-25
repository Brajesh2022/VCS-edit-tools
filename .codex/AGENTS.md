# VCS CLI — Codex Agent Instructions

Native file-editing tools are unreliable in this environment. Use the `vcs` CLI (installed at `~/.local/bin/vcs`) for all file operations.

## Commands (quick reference)

```
READ:      vcs read <filepath> [start-end] [--symbol <name>]
REPLACE:   vcs replace <filepath> <blob> <start-end> << 'EOF' ... EOF
INSERT:    vcs insert <filepath> <blob> <line> << 'EOF' ... EOF
DELETE:    vcs delete <filepath> <blob> <start-end>        (line range)
DELETE:    vcs delete <filepath>                            (file or entire directory)
CREATE:    vcs create <filepath> << 'EOF' ... EOF           (new file with content)
BATCH:     vcs batch << 'EOF'  JSON array  EOF              (BOTH filepath AND blob per edit)
DIFF:      vcs diff <filepath> <blob>
SKELETON:  vcs skeleton <filepath> [start-end]              (structural view; use first for files >800 lines)
TREE:      vcs tree [path] [--depth N] [--all]              (ALWAYS use instead of `ls`/`find`)
GREP:      vcs grep <query> [path] [-i]
FMT:       vcs fmt [--check] [path]
TEST:      vcs test <cmd> [path]
STATUS:    vcs status
```

## Critical Rules

**1. Never auto-calculate lines.**
Use exact line numbers from what you already read. No guessing or recalculating positions after edits.

**2. Both filepath AND blob required for edits.**
`replace`, `insert`, `delete` (line-range), and `batch` all require BOTH `<filepath>` AND `<blob>`.
- Not just blob. Not just filepath. Both.
- Blob proves you read the file. Filepath confirms which file.
- Missing either → command is rejected.

**3. Re-read rule.**
- Editing a region untouched since your last read → reuse the old blob. No re-read needed, no new blob needed. Just start the edit.
- Editing a region you already modified → you MUST re-read that portion first. Recommended flow: guess the expected line range of the portion → `vcs read <filepath> <range>` → get a fresh blob → perform the edit with that fresh blob.

**4. Batch requires blob too.**
No blob in any batch edit → entire batch is rejected.

**5. Replace / Insert / Batch output — just `ok`.**
On success the CLI prints:
```
status: ok
```
Nothing else. Don't expect a diff, new blob echo, or line totals.

**6. Conflict response — simple human message.**
On a genuine conflict (file was modified externally since your read) the CLI prints:
```
Merge conflict detected. Please read the latest version and try again.
```
No diff, no conflicting lines, no technical details. Just re-read and retry.

## Workflow Tips

- For files >800 lines, run `vcs skeleton <filepath>` first to get the structural overview.
- Use `vcs tree` to explore directory structure (it skips `.git`, `node_modules`, etc. and caps at 10 items per dir for speed).
- Heredoc with single-quoted EOF is required for multi-line content (`<< 'EOF'`), so `$VAR` and `$(cmd)` in your content are preserved literally.
- After a successful edit, the old blob is invalidated — re-read the file if you need to make another edit to a region you just changed.
