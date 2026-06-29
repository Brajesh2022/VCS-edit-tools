with open("cli.py", "r") as f:
    content = f.read()
    
content = content.replace("  status    [--prune]                               List blob→filepath mappings (or prune stale entries)",
"""  status    [--prune]                               List blob→filepath mappings (or prune stale entries)
  undo      <filepath> [blob]                       Roll back to a previous state
  history   <filepath>                              List previous blobs for a file""")

with open("cli.py", "w") as f:
    f.write(content)
