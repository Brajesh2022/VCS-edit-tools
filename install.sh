#!/usr/bin/env bash
# VCS Edit CLI - Installer
#
# A robust, terminal-aware installer that:
#   • Works headless (-y / --plugins) for CI/CD
#   • Works interactively with a real TTY (arrow keys + SPACE + ENTER)
#   • Falls back to a numbered prompt when no TTY is available
#   • Always gives visible feedback after ENTER so users never wonder
#     whether the script is doing anything
set -Eeuo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  BOLD="\033[1m"
  DIM="\033[2m"
  GREEN="\033[32m"
  RED="\033[31m"
  YELLOW="\033[33m"
  CYAN="\033[36m"
  RESET="\033[0m"
else
  BOLD="" DIM="" GREEN="" RED="" YELLOW="" CYAN="" RESET=""
fi

# ── Helpers ───────────────────────────────────────────────────────────────────
info()    { printf '%b\n' " ${CYAN}[..]${RESET} ${DIM}$*${RESET}"; }
ok()      { printf '%b\n' " ${GREEN}[OK]${RESET} $*"; }
warn()    { printf '%b\n' " ${YELLOW}[!]${RESET} $*"; }
die()     {
  printf "\033[?25h" >&2  # always restore cursor before dying
  printf '\n%b\n' " ${RED}[ERR]${RESET} ${1:-Installation failed.}" >&2
  exit 1
}
divider() { printf '%b\n' "${DIM}────────────────────────────────────────${RESET}"; }

# spinner <pid> <message>
# Shows a rotating spinner while background process <pid> is running.
# Returns the exit status of the waited-for process.
spinner() {
  local pid=$1
  local msg=$2
  local spinstr='|/-\'
  printf "\033[?25l"  # hide cursor
  while kill -0 "$pid" 2>/dev/null; do
    local temp=${spinstr#?}
    printf "\r\033[K %b[%c]%b %b%s%b" "$CYAN" "$spinstr" "$RESET" "$DIM" "$msg" "$RESET"
    local spinstr=$temp${spinstr%"$temp"}
    sleep 0.1
  done
  local exit_status=0
  wait "$pid" || exit_status=$?
  if [ "$exit_status" -eq 0 ]; then
    printf "\r\033[K %b[OK]%b %s\n" "$GREEN" "$RESET" "$msg"
  else
    printf "\r\033[K %b[ERR]%b %s\n" "$RED" "$RESET" "$msg"
  fi
  printf "\033[?25h"  # show cursor
  return "$exit_status"
}

# ── Header ────────────────────────────────────────────────────────────────────
echo ""
printf "  %bVCS Edit Tools%b\n" "${BOLD}${CYAN}" "${RESET}"
printf "  %bUniversal Installer%b\n" "${DIM}" "${RESET}"
echo ""
divider

# ── Initialization ────────────────────────────────────────────────────────────
INSTALL_DIR="$HOME/.VCS-edit-tools"
BIN_DIR="$HOME/.local/bin"

OS="$(uname -s)"
case "${OS}" in
    Linux*)     MACHINE=Linux;;
    Darwin*)    MACHINE=Mac;;
    CYGWIN*)    MACHINE=Cygwin;;
    MINGW*)     MACHINE=MinGw;;
    *)          MACHINE="UNKNOWN:${OS}";;
esac

if [[ -n "${PREFIX:-}" && "${PREFIX:-}" == *"/usr"* && "${OS:-}" == "Linux" ]]; then
    BIN_DIR="${PREFIX}/bin"
    ok "Environment: Termux"
else
    ok "Environment: $MACHINE"
fi

NON_INTERACTIVE=false
SELECTED_PLUGINS=""

while [[ "$#" -gt 0 ]]; do
    case $1 in
        -y|--yes) NON_INTERACTIVE=true ;;
        --plugins)
            [[ $# -ge 2 ]] || die "--plugins requires a value (e.g. --plugins antigravity,claude)"
            SELECTED_PLUGINS="$2"
            shift
            ;;
        --install-plugins)
            SELECTED_PLUGINS="antigravity"
            ;;
        --no-plugins)
            SELECTED_PLUGINS="none"
            ;;
        --list-plugins)
            echo "Available plugins: antigravity, claude, codex, all"
            exit 0
            ;;
        -h|--help)
            cat <<'EOF'
VCS Edit Tools — Installer

Usage:
  install.sh [options]

Options:
  -y, --yes              Non-interactive mode (skip prompts, fail if deps missing)
  --plugins LIST         Comma-separated plugin list: antigravity,claude,codex,all,none
  --install-plugins      Shortcut for --plugins antigravity
  --no-plugins           Skip plugin installation entirely
  --list-plugins         Print available plugin names and exit
  -h, --help             Show this help and exit

Interactive mode (default when stdin is a TTY):
  Up/Down or k/j         Move cursor
  Space                  Toggle selection
  Enter                  Confirm and install
  q / Esc                Cancel (skips plugins)
EOF
            exit 0
            ;;
        *) die "Unknown parameter passed: $1" ;;
    esac
    shift
done

# ── Dependencies ──────────────────────────────────────────────────────────────
deps_missing=false
for cmd in git python3; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        deps_missing=true
    fi
done

if [ "$deps_missing" = true ]; then
    if [ "$NON_INTERACTIVE" = true ]; then
        die "Non-interactive mode: please install missing dependencies (git, python3)."
    fi
    printf "\n"
    info "Missing dependencies: git, python3"

    # Open /dev/tty for the prompt if stdin isn't a TTY (e.g. curl|bash)
    deps_choice=""
    if [ -t 0 ]; then
        read -rp "  Install missing dependencies automatically? (y/n) " deps_choice
    elif [ -c /dev/tty ]; then
        read -rp "  Install missing dependencies automatically? (y/n) " deps_choice </dev/tty
    else
        deps_choice="n"
    fi

    if [[ "$deps_choice" =~ ^[Yy]$ ]]; then
        if command -v pkg >/dev/null 2>&1; then
            pkg install -y git python >/dev/null 2>&1 &
            spinner $! "Installing via pkg..." || die "Failed to install via pkg."
        elif command -v apt >/dev/null 2>&1; then
            (sudo apt update && sudo apt install -y git python3) >/dev/null 2>&1 &
            spinner $! "Installing via apt..." || die "Failed to install via apt."
        elif command -v brew >/dev/null 2>&1; then
            brew install git python3 >/dev/null 2>&1 &
            spinner $! "Installing via brew..." || die "Failed to install via brew."
        else
            die "Could not detect package manager. Install manually."
        fi
    else
        die "Dependencies required."
    fi
fi
ok "Dependencies met"

# ── Clone / Update ────────────────────────────────────────────────────────────
if [ -d "$INSTALL_DIR" ]; then
    (git -C "$INSTALL_DIR" fetch origin main && git -C "$INSTALL_DIR" reset --hard origin/main || git -C "$INSTALL_DIR" pull origin master) >/dev/null 2>&1 &
    spinner $! "Updating repository..." || die "Failed to update repo."
else
    git clone https://github.com/Brajesh2022/VCS-edit-tools.git "$INSTALL_DIR" >/dev/null 2>&1 &
    spinner $! "Cloning repository..." || die "Failed to clone repo."
fi

# ── Install ───────────────────────────────────────────────────────────────────
mkdir -p "$BIN_DIR"
chmod +x "$INSTALL_DIR/vcs"

if command -v termux-fix-shebang >/dev/null 2>&1; then
    termux-fix-shebang "$INSTALL_DIR/vcs"
fi

ln -sf "$INSTALL_DIR/vcs" "$BIN_DIR/vcs"
ok "CLI linked to $BIN_DIR/vcs"

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    warn "$BIN_DIR is not in PATH."
    warn "Add export PATH=\"\$PATH:$BIN_DIR\" to your shell profile."
fi

# ── Plugin selection ──────────────────────────────────────────────────────────
# Plugin definitions: id | label | source-path-check | target-dir
PLUGIN_IDS=("antigravity" "claude" "codex")
PLUGIN_LABELS=(
    "Antigravity (.agy)"
    "Claude     (.claude/rules/vcs-cli.md)"
    "Codex      (~/.codex/AGENTS.md)"
)

# Resolve whether we have an interactive TTY available.
# We return true only if EITHER stdin is a TTY OR we successfully opened
# /dev/tty as FD 3 (which is the curl|bash case). Just checking
# `[ -c /dev/tty ]` is NOT enough — the device file can exist on systems
# with no controlling terminal (CI runners, containers) and then opening
# it fails at runtime, which is exactly what was crashing the old script.
have_tty() {
    if [ -t 0 ]; then
        return 0
    fi
    if [ -n "${TTY_FD:-}" ]; then
        return 0
    fi
    return 1
}

# Open /dev/tty as FD 3 so we can read keys without disturbing stdin.
# This works for both `bash install.sh` and `curl ... | bash`.
# We test-open in a subshell first so a missing controlling terminal doesn't
# crash the script under `set -e` (CI runners, containers, etc.).
TTY_FD=""
if [ -c /dev/tty ]; then
    if (exec 3</dev/tty) 2>/dev/null; then
        exec 3</dev/tty 2>/dev/null
        TTY_FD=3
    fi
fi

# Read a single key from the best available input source.
# Sets the global KEY variable. Returns 0 on success, 1 on EOF.
read_key() {
    KEY=""
    if [ -n "$TTY_FD" ]; then
        IFS= read -rsn1 -u "$TTY_FD" KEY || return 1
    else
        IFS= read -rsn1 KEY || return 1
    fi
    return 0
}

# Interactive multi-select menu.
# Uses arrow keys + SPACE to toggle + ENTER to confirm.
# Sets SELECTED_PLUGINS on exit.
interactive_plugin_menu() {
    local n=${#PLUGIN_LABELS[@]}
    local cursor=0
    # Pre-select antigravity (matches previous default behavior)
    local selections=()
    local i
    for (( i=0; i<n; i++ )); do
        if [ "$i" -eq 0 ]; then
            selections+=("1")
        else
            selections+=("0")
        fi
    done

    # If we have no TTY_FD, we cannot do raw key reading — fall through to numbered prompt
    if [ -z "$TTY_FD" ] && [ ! -t 0 ]; then
        return 1
    fi

    # Hide cursor for the duration of the menu (best-effort; harmless on non-TTYs)
    printf "\033[?25l"
    echo "  (Use ↑/↓ or k/j to move, SPACE to toggle, ENTER to confirm, q to skip)"
    echo ""

    # Print initial menu — we'll redraw in place each iteration
    _draw_menu() {
        local i
        # Move cursor up to the first menu line (we printed n lines last time)
        # Then redraw each line in place
        printf "\033[%dA" "$n"
        for (( i=0; i<n; i++ )); do
            local prefix
            if [ "$i" -eq "$cursor" ]; then
                prefix="${CYAN}${BOLD}  > "
            else
                prefix="    "
            fi
            local box
            if [ "${selections[$i]}" = "1" ]; then
                box="[x]"
            else
                box="[ ]"
            fi
            printf "\r\033[K%b%s %s%b\n" "$prefix" "$box" "${PLUGIN_LABELS[$i]}" "${RESET}"
        done
    }

    # Print menu once (without trying to move up — there's nothing above yet)
    for (( i=0; i<n; i++ )); do
        local prefix
        if [ "$i" -eq "$cursor" ]; then
            prefix="${CYAN}${BOLD}  > "
        else
            prefix="    "
        fi
        local box
        if [ "${selections[$i]}" = "1" ]; then
            box="[x]"
        else
            box="[ ]"
        fi
        printf "\r\033[K%b%s %s%b\n" "$prefix" "$box" "${PLUGIN_LABELS[$i]}" "${RESET}"
    done

    while true; do
        KEY=""
        if ! read_key; then
            break
        fi

        # Handle multi-byte escape sequences for arrow keys.
        # Arrow keys send ESC [ A/B/C/D. We already consumed ESC; now read
        # the rest with a short timeout so a lone ESC (cancel) still works.
        case "$KEY" in
            $'\e'|$'\x1b')
                local k1=""
                local k2=""
                if [ -n "$TTY_FD" ]; then
                    IFS= read -rsn1 -t 0.15 -u "$TTY_FD" k1 || true
                else
                    IFS= read -rsn1 -t 0.15 k1 || true
                fi
                if [[ "$k1" == "[" || "$k1" == "O" ]]; then
                    if [ -n "$TTY_FD" ]; then
                        IFS= read -rsn1 -t 0.15 -u "$TTY_FD" k2 || true
                    else
                        IFS= read -rsn1 -t 0.15 k2 || true
                    fi
                    case "$k2" in
                        A|D) cursor=$(( (cursor - 1 + n) % n )) ;;
                        B|C) cursor=$(( (cursor + 1) % n )) ;;
                    esac
                elif [ -z "$k1" ]; then
                    # Lone ESC with no follow-up → cancel
                    break
                fi
                ;;
            " ")
                # Toggle current selection
                if [ "${selections[$cursor]}" = "1" ]; then
                    selections[$cursor]="0"
                else
                    selections[$cursor]="1"
                fi
                ;;
            ""|$'\n'|$'\r')
                # ENTER — break out of the loop
                break
                ;;
            q|Q)
                # Cancel — skip plugins entirely
                for (( i=0; i<n; i++ )); do
                    selections[$i]="0"
                done
                break
                ;;
            1|2|3)
                # Number keys also toggle (accessibility / fallback)
                local idx=$(( KEY - 1 ))
                if [ "$idx" -ge 0 ] && [ "$idx" -lt "$n" ]; then
                    if [ "${selections[$idx]}" = "1" ]; then
                        selections[$idx]="0"
                    else
                        selections[$idx]="1"
                    fi
                fi
                ;;
            *)
                # Ignore unknown keys
                ;;
        esac

        # Redraw menu in place
        _draw_menu
    done

    # Restore cursor
    printf "\033[?25h"

    # Build the comma-separated list
    SELECTED_PLUGINS=""
    for (( i=0; i<n; i++ )); do
        if [ "${selections[$i]}" = "1" ]; then
            SELECTED_PLUGINS="${SELECTED_PLUGINS}${PLUGIN_IDS[$i]},"
        fi
    done
    [[ -z "$SELECTED_PLUGINS" ]] && SELECTED_PLUGINS="none"

    return 0
}

# Numbered fallback menu (used when no TTY is available at all).
numbered_plugin_menu() {
    echo ""
    echo "  Available AI Agent Integrations:"
    echo "    1) Antigravity (.agy)"
    echo "    2) Claude     (.claude/rules/vcs-cli.md)"
    echo "    3) Codex      (~/.codex/AGENTS.md)"
    echo "    4) All of the above"
    echo "    5) Skip plugins"
    echo ""

    local choice=""
    # Prefer /dev/tty (FD 3) when available so curl|bash works; fall back to stdin.
    # We use TTY_FD (already validated to be openable) rather than `[ -c /dev/tty ]`
    # because the latter is true even on systems with no controlling terminal.
    if [ -n "${TTY_FD:-}" ]; then
        IFS= read -r -u "$TTY_FD" -p "  Select option [1-5] (default 4): " choice || choice="4"
    else
        IFS= read -r -p "  Select option [1-5] (default 4): " choice || choice="4"
    fi

    case "${choice:-4}" in
        1) SELECTED_PLUGINS="antigravity," ;;
        2) SELECTED_PLUGINS="claude," ;;
        3) SELECTED_PLUGINS="codex," ;;
        4) SELECTED_PLUGINS="antigravity,claude,codex," ;;
        5|"") SELECTED_PLUGINS="none" ;;
        *)
            # Allow comma-separated like "1,2"
            SELECTED_PLUGINS=""
            local cleaned="${choice//[^0-9,]/}"
            IFS=',' read -ra parts <<< "$cleaned"
            for p in "${parts[@]}"; do
                case "$p" in
                    1) SELECTED_PLUGINS="${SELECTED_PLUGINS}antigravity," ;;
                    2) SELECTED_PLUGINS="${SELECTED_PLUGINS}claude," ;;
                    3) SELECTED_PLUGINS="${SELECTED_PLUGINS}codex," ;;
                esac
            done
            [[ -z "$SELECTED_PLUGINS" ]] && SELECTED_PLUGINS="none"
            ;;
    esac
}

# Run plugin selection if needed
if [ -z "$SELECTED_PLUGINS" ] && [ "$NON_INTERACTIVE" = false ]; then
    echo ""
    divider
    printf "  %bAI Agent Integrations%b\n" "${BOLD}" "${RESET}"

    if have_tty; then
        if ! interactive_plugin_menu; then
            # Menu returned non-zero (no raw key support) — use numbered fallback
            numbered_plugin_menu
        fi
    else
        numbered_plugin_menu
    fi

    # ── CRITICAL: visual feedback after ENTER ────────────────────────────────
    # This is what was missing — users pressed ENTER and saw nothing happen.
    # Now we explicitly echo what was selected and what's about to happen.
    echo ""
    if [ "$SELECTED_PLUGINS" = "none" ]; then
        info "No plugins selected. Skipping AI agent integration."
    else
        local_count=0
        IFS=',' read -ra _chosen <<< "$SELECTED_PLUGINS"
        for _p in "${_chosen[@]}"; do
            [ -z "$_p" ] && continue
            local_count=$((local_count + 1))
        done
        info "Selected $local_count plugin(s): ${SELECTED_PLUGINS%,}"
        info "Installing plugins..."
        echo ""
    fi
    divider
fi

# ── Antigravity plugin ───────────────────────────────────────────────────────
if [[ "$SELECTED_PLUGINS" == *"antigravity"* || "$SELECTED_PLUGINS" == *"all"* ]]; then
    AGY_PLUGINS_DIR="$HOME/.gemini/config/plugins/vcs-edit"
    (
        mkdir -p "$AGY_PLUGINS_DIR"
        if [ -d "$INSTALL_DIR/.agy" ]; then
            # Use find to avoid glob-no-match failures on empty dirs
            (cd "$INSTALL_DIR/.agy" && cp -r . "$AGY_PLUGINS_DIR/")
        fi
    ) >/dev/null 2>&1 &
    if spinner $! "Installing Antigravity plugin..."; then
        if [ -d "$INSTALL_DIR/.agy" ]; then
            ok "Antigravity plugin installed → $AGY_PLUGINS_DIR"
        else
            warn "Antigravity plugin source not found in $INSTALL_DIR/.agy"
        fi
    else
        warn "Antigravity plugin install failed."
    fi
fi

# ── Claude integration: just drop vcs-cli.md into ~/.claude/rules/ ───────────
# No hooks, no plugins. Claude Code automatically loads rules files from
# ~/.claude/rules/ as part of its system prompt — perfect for our use case.
if [[ "$SELECTED_PLUGINS" == *"claude"* || "$SELECTED_PLUGINS" == *"all"* ]]; then
    CLAUDE_RULES_DIR="$HOME/.claude/rules"
    (
        mkdir -p "$CLAUDE_RULES_DIR"
        if [ -f "$INSTALL_DIR/.claude/vcs-cli.md" ]; then
            cp -f "$INSTALL_DIR/.claude/vcs-cli.md" "$CLAUDE_RULES_DIR/vcs-cli.md"
        fi
    ) >/dev/null 2>&1 &
    if spinner $! "Installing Claude rules..."; then
        if [ -f "$INSTALL_DIR/.claude/vcs-cli.md" ]; then
            ok "Claude rules installed → $CLAUDE_RULES_DIR/vcs-cli.md"

            # Clean up legacy hooks/payload from previous installs (v1 had a hooks system).
            LEGACY_PLUGIN_DIR="$HOME/.claude/plugins/vcs-edit"
            if [ -d "$LEGACY_PLUGIN_DIR" ]; then
                rm -rf "$LEGACY_PLUGIN_DIR"
                info "Removed legacy Claude hooks/plugins from $LEGACY_PLUGIN_DIR"
            fi

            # Remove the UserPromptSubmit hook entry from settings.json if it points at vcs-edit.
            if [ -f "$HOME/.claude/settings.json" ]; then
                python3 -c "
import json, os, sys
sp = os.path.expanduser('~/.claude/settings.json')
try:
    with open(sp) as f:
        s = json.load(f)
except Exception:
    sys.exit(0)
changed = False
hooks = s.get('hooks', {})
if 'UserPromptSubmit' in hooks:
    kept = [h for h in hooks['UserPromptSubmit'] if 'vcs-edit' not in str(h)]
    if len(kept) != len(hooks['UserPromptSubmit']):
        hooks['UserPromptSubmit'] = kept
        if not kept:
            del hooks['UserPromptSubmit']
        changed = True
if changed:
    if not hooks:
        del s['hooks']
    with open(sp, 'w') as f:
        json.dump(s, f, indent=2)
    print('    cleaned legacy hook from ~/.claude/settings.json')
" || true
            fi
        else
            warn "Claude rules source not found in $INSTALL_DIR/.claude/vcs-cli.md"
        fi
    else
        warn "Claude rules install failed."
    fi
fi

# ── Codex integration: ~/.codex/AGENTS.md (with override logic) ──────────────
# Codex does not have a hooks/rules system. It loads ~/.codex/AGENTS.md at
# session start. We respect the user's hierarchy:
#   1. If ~/.codex/AGENTS.override.md exists  → do nothing (user has overridden)
#   2. Else if ~/.codex/AGENTS.md exists       → do nothing (user already has one)
#   3. Else                                    → create AGENTS.md with VCS instructions
if [[ "$SELECTED_PLUGINS" == *"codex"* || "$SELECTED_PLUGINS" == *"all"* ]]; then
    CODEX_DIR="$HOME/.codex"
    OVERRIDE_FILE="$CODEX_DIR/AGENTS.override.md"
    AGENTS_FILE="$CODEX_DIR/AGENTS.md"

    # Pre-flight: decide what we're going to do so we can show a meaningful spinner message
    codex_msg="Checking Codex AGENTS.md..."
    if [ -f "$OVERRIDE_FILE" ]; then
        codex_msg="Codex: AGENTS.override.md detected — respecting user override."
    elif [ -f "$AGENTS_FILE" ]; then
        codex_msg="Codex: AGENTS.md already exists — leaving untouched."
    elif [ -f "$INSTALL_DIR/.codex/AGENTS.md" ]; then
        codex_msg="Installing Codex AGENTS.md..."
    else
        codex_msg="Codex: source template not found."
    fi

    (
        mkdir -p "$CODEX_DIR"
        if [ ! -f "$OVERRIDE_FILE" ] && [ ! -f "$AGENTS_FILE" ] && [ -f "$INSTALL_DIR/.codex/AGENTS.md" ]; then
            cp -f "$INSTALL_DIR/.codex/AGENTS.md" "$AGENTS_FILE"
        fi
    ) >/dev/null 2>&1 &
    if spinner $! "$codex_msg"; then
        if [ -f "$OVERRIDE_FILE" ]; then
            ok "Codex: AGENTS.override.md detected — respecting user override, no changes made."
        elif [ -f "$AGENTS_FILE" ]; then
            # Did we just create it, or was it already there?
            if [ -f "$INSTALL_DIR/.codex/AGENTS.md" ] && ! diff -q "$INSTALL_DIR/.codex/AGENTS.md" "$AGENTS_FILE" >/dev/null 2>&1; then
                # Files differ → user had their own
                ok "Codex: AGENTS.md already exists — leaving it untouched (user has their own)."
                info "        To install VCS instructions, rename your file to AGENTS.override.md"
                info "        first, then re-run this installer."
            elif [ -f "$INSTALL_DIR/.codex/AGENTS.md" ]; then
                ok "Codex: created $AGENTS_FILE with VCS CLI instructions."
            else
                ok "Codex: AGENTS.md already exists — leaving it untouched."
            fi
        else
            warn "Codex: source template not found at $INSTALL_DIR/.codex/AGENTS.md"
        fi
    else
        warn "Codex setup failed."
    fi
fi

# Close TTY_FD if we opened it
if [ -n "$TTY_FD" ]; then
    exec 3<&-
fi

# ── Complete ──────────────────────────────────────────────────────────────────
echo ""
printf '%b\n' "${GREEN}${BOLD}Installation Complete.${RESET}"
divider
info "Try running: ${BOLD}vcs --help${RESET}"
echo ""
