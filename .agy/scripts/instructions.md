# 🚨 Critical Instructions: User thinks system ms given tools are less token efficient, that's why for your activities user want's you to use Following CLI

# READ
vcs read <filepath> [start-end]          # >800 lines → auto-skeleton

# REPLACE
vcs replace <target> <start-end> << 'EOF'
content
EOF

# INSERT  (before given line, 1-indexed)
vcs insert <target> <line> << 'EOF'
content
EOF

# DELETE
vcs delete <target> <start-end>

# BATCH  (atomic, each op 3-way-merge protected)
vcs batch << 'EOF'
[
  {"op":"replace","blob":"<hash>","range":"8-50","content":"..."},
  {"op":"insert", "blob":"<hash>","line":10,     "content":"..."},
  {"op":"delete", "blob":"<hash>","range":"5-8"}
]
EOF

# DIFF      vcs diff <target>            # blob snapshot vs disk
# SKELETON  vcs skeleton <filepath> [start-end]   # use before editing >800-line files
# TREE      vcs tree [path] [--depth N]  # .gitignore-aware, default depth=2
# GREP      vcs grep <query> [path] [-i] # shows enclosing fn/class context
# FMT       vcs fmt [--check] [path]     # auto-formatter; --check=dry run
# TEST      vcs test <cmd> [path]        # runs tests, failures only
# STATUS    vcs status                   # list all blob→filepath mappings

# BLOB = file snapshot at read-time. Use hash OR filepath as <target>.
# HEREDOC: always << 'EOF' for multiline — prevents $, backtick, quote eval.
