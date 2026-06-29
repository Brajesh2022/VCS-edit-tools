import sys

with open("cli.py", "r") as f:
    lines = f.readlines()

# find COMMANDS
cmd_start = -1
for i, line in enumerate(lines):
    if line.startswith("COMMANDS = {"):
        cmd_start = i
        break

# find def main
main_start = -1
for i, line in enumerate(lines):
    if line.startswith("def main()"):
        main_start = i
        break
        
# find _do_undo
undo_start = -1
for i, line in enumerate(lines):
    if line.startswith("def _do_undo"):
        undo_start = i
        break

if undo_start == -1 or cmd_start == -1:
    print("could not find required sections")
    sys.exit(1)

# slice out the undo/history stuff
undo_code = lines[undo_start:]

# insert undo code before COMMANDS
new_lines = lines[:cmd_start] + undo_code + lines[cmd_start:undo_start]

# now find COMMANDS again to inject undo and history
cmd_start = -1
for i, line in enumerate(new_lines):
    if line.startswith("COMMANDS = {"):
        cmd_start = i
        break

# insert into COMMANDS
new_lines.insert(cmd_start + 1, '    "undo": cmd_undo,\n')
new_lines.insert(cmd_start + 2, '    "history": cmd_history,\n')

with open("cli.py", "w") as f:
    f.writelines(new_lines)
