import re

with open("README.md", "r") as f:
    text = f.read()

# 1. Update Usage
text = text.replace(
    "vcs replace <blob_hash> <START-END> <content_file>",
    "vcs replace <target> <START-END> <content_file>"
)
text = text.replace(
    "vcs insert <blob_hash> <LINE> <content_file>",
    "vcs insert <target> <LINE> <content_file>"
)
text = text.replace(
    "vcs delete <blob_hash> <START-END>",
    "vcs delete <target> <START-END>"
)

# 2. Update auto_merged block (since my replace failed)
old_auto = '''```bash
$ vcs replace <old_blob> 4-5 /tmp/new.py
{
  "status": "auto_merged",
  "new_blob": "...",
  "path": "sample.py",
  "new_total_lines": 6,
  "merged_regions": [{"start": 1, "end": 1}]
}
```'''
new_auto = '''```bash
$ vcs replace <target> 4-5 /tmp/new.py
{
  "status": "auto_merged",
  "new_blob": "c659b6e1",
  "merged_regions": [{"start": 1, "end": 1}]
}
```'''
text = text.replace(old_auto, new_auto)

# 3. Update insert
old_insert = '''### `vcs insert` — insert content before a line

```bash
vcs insert <blob_hash> <LINE> <content_file>
```'''
new_insert = '''### `vcs insert` — insert content before a line

```bash
vcs insert <target> <LINE> <content_file>
```'''
text = text.replace(old_insert, new_insert)

# 4. Update delete
old_delete = '''### `vcs delete` — delete a line range

```bash
vcs delete <blob_hash> <START-END>
```'''
new_delete = '''### `vcs delete` — delete a line range

```bash
vcs delete <target> <START-END>
```'''
text = text.replace(old_delete, new_delete)

# 5. Update diff
old_diff = '''### `vcs diff` — inspect what changed since a blob

```bash
vcs diff <blob_hash> <filepath>
```'''
new_diff = '''### `vcs diff` — inspect what changed since a blob

```bash
vcs diff <target>
```'''
text = text.replace(old_diff, new_diff)

with open("README.md", "w") as f:
    f.write(text)
