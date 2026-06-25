#!/usr/bin/env bash
# VCS Edit CLI - Installer (v2.1.1 — redesigned interactive menu)
set -Eeuo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  BOLD="\033[1m"
  DIM="\033[2m"
  GREEN="\033[32m"
  RED="\033[31m"
  CYAN="\033[36m"
  YELLOW="\033[33m"
  RESET="\033[0m"
else
  BOLD="" DIM="" GREEN="" RED="" CYAN="" YELLOW="" RESET=""
fi

# ── Helpers ───────────────────────────────────────────────────────────────────
info()    { printf '%b\n' " ${CYAN}[..]${RESET} ${DIM}$*${RESET}"; }
ok()      { printf '%b\n' " ${GREEN}[OK]${RESET} $*"; }
die()     {
  printf "\033[?25h" >&2
  printf '\n%b\n' " ${RED}[ERR]${RESET} ${1:-Installation failed.}" >&2
  exit 1
}
warn()    { printf '%b\n' " ${YELLOW}[!]${RESET} $*"; }
divider() { printf '%b\n' "${DIM}────────────────────────────────────────${RESET}"; }

spinner() {
  local pid=$1
  local msg=$2
  local spinstr='\|/-'
  printf "\033[?25l" # Hide cursor
  while kill -0 "$pid" 2>/dev/null; do
    local temp=${spinstr#?}
    printf "\r\033[K %b[%c]%b %b%s%b" "$CYAN" "$spinstr" "$RESET" "$DIM" "$msg" "$RESET"
    local spinstr=$temp${spinstr%"$temp"}
    sleep 0.1
  done
  local exit_status=0
  wait "$pid" || exit_status=$?
  if [ $exit_status -eq 0 ]; then
    printf "\r\033[K %b[OK]%b %s\n" "$GREEN" "$RESET" "$msg"
  else
    printf "\r\033[K %b[ERR]%b %s\n" "$RED" "$RESET" "$msg"
  fi
  printf "\033[?25h" # Show cursor
  return $exit_status
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
USE_MENU=true

while [[ "$#" -gt 0 ]]; do
    case $1 in
        -y|--yes) NON_INTERACTIVE=true ;;
        --plugins)
            SELECTED_PLUGINS="$2"
            shift
            ;;
        --install-plugins)
            SELECTED_PLUGINS="antigravity"
            ;;
        --no-plugins)
            SELECTED_PLUGINS="none"
            ;;
        --no-menu)
            USE_MENU=false
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
    read -p "  Install missing dependencies automatically? (y/n) " -n 1 -r choice </dev/tty || choice="n"
    printf "\n"
    if [[ "$choice" =~ ^[Yy]$ ]]; then
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

# ── Plugin selection menu (redesigned) ────────────────────────────────────────
# The menu is a self-contained function. It sets the global SELECTED_PLUGINS
# variable. Falls back to a numbered menu if no TTY or if --no-menu was passed.
select_plugins_interactive() {
    local options=("Antigravity (.agy)" "Claude (.claude/rules/)" "Codex (~/.codex/AGENTS.md)")
    local plugin_ids=("antigravity" "claude" "codex")
    local descriptions=(
        "Hooks into Gemini's PreInvocation (payload.json + hooks.json)"
        "Drops vcs-cli.md into ~/.claude/rules/ (no hooks, no plugins)"
        "Creates ~/.codex/AGENTS.md (respects existing override)"
    )
    local selections=(1 0 0)   # default: Antigravity pre-selected
    local cursor=0
    local n=${#options[@]}

    # Decide which path to take
    if [[ "$USE_MENU" == "false" ]] || [[ ! -t 0 ]] || [[ ! -t 1 ]]; then
        _select_plugins_numbered "${options[@]}" "${plugin_ids[@]}" "${descriptions[@]}"
        return
    fi

    # ── TUI menu ──────────────────────────────────────────────────────────────
    # Disable set -e locally so read timeouts don't kill the script.
    # This is THE fix for the "nothing happens on ENTER" bug — under set -e,
    # `read -rsn1 -t 0.1` returning non-zero on timeout would silently exit.
    local _had_set_e=false
    local _had_set_u=false
    [[ "${-:-}" == *e* ]] && _had_set_e=true
    [[ "${-:-}" == *u* ]] && _had_set_u=true
    set +e
    set +u  # also disable nounset — k1/k2 may be unset on timeout

    # Ensure cursor is visible if we exit early
    trap 'printf "\033[?25h" >&2' EXIT RETURN

    # Hide cursor for the menu
    printf '\033[?25l' >&2

    # Print instructions + initial menu
    printf '  %bUse ↑/↓ to move, SPACE to toggle, ENTER to confirm, q to skip%b\n' "$DIM" "$RESET" >&2
    _draw_tui_menu "$cursor" "$n" \
        "${options[0]}" "${options[1]}" "${options[2]}" \
        "${selections[0]}" "${selections[1]}" "${selections[2]}" >&2

    local key="" k1="" k2=""
    while true; do
        # Read 1 key, no echo, no delimiter wait. This is the main blocking read.
        # read returns 0 on success (got a char), non-zero on EOF/error.
        IFS= read -rsn1 key 2>/dev/null
        local rc=$?

        # If read failed (EOF, closed stdin), treat as confirm with current selection
        if [[ $rc -ne 0 && -z "$key" ]]; then
            break
        fi

        # Handle escape sequences (arrow keys send ESC [ A/B/C/D)
        if [[ "$key" == $'\x1b' || "$key" == $'\e' ]]; then
            k1=""
            k2=""
            # Try to read '[' or 'O' (with short timeout so plain ESC still works)
            IFS= read -rsn1 -t 0.3 k1 2>/dev/null || true
            if [[ "$k1" == "[" || "$k1" == "O" ]]; then
                IFS= read -rsn1 -t 0.3 k2 2>/dev/null || true
                case "$k2" in
                    A|D) cursor=$(( (cursor - 1 + n) % n )) ;;
                    B|C) cursor=$(( (cursor + 1) % n )) ;;
                esac
            else
                # Plain ESC (no arrow) — treat as cancel/skip
                selections=(0 0 0)
                break
            fi
        elif [[ "$key" == " " ]]; then
            # Toggle current selection
            if [[ ${selections[$cursor]} -eq 1 ]]; then
                selections[$cursor]=0
            else
                selections[$cursor]=1
            fi
        elif [[ -z "$key" || "$key" == $'\n' || "$key" == $'\r' ]]; then
            # ENTER — confirm current selections
            break
        elif [[ "$key" == "q" || "$key" == "Q" ]]; then
            # q — skip all plugins
            selections=(0 0 0)
            break
        elif [[ "$key" == $'\x03' ]]; then
            # Ctrl-C — restore cursor and exit
            printf '\033[?25h' >&2
            trap - EXIT
            # Restore set options before exiting
            [[ "$_had_set_e" == "true" ]] && set -e
            [[ "$_had_set_u" == "true" ]] && set -u
            exit 130
        fi

        # Redraw: move cursor up n lines, clear, reprint
        _redraw_tui_menu "$cursor" "$n" \
            "${options[0]}" "${options[1]}" "${options[2]}" \
            "${selections[0]}" "${selections[1]}" "${selections[2]}" >&2
    done

    # Show cursor again
    printf '\033[?25h' >&2
    trap - EXIT

    # Clear the menu lines (move up n+1 lines — the +1 is for the instructions line)
    printf '\033[%dA\033[J' "$((n + 1))" >&2

    # Restore shell options
    [[ "$_had_set_e" == "true" ]] && set -e
    [[ "$_had_set_u" == "true" ]] && set -u

    # Build SELECTED_PLUGINS from selections
    SELECTED_PLUGINS=""
    local i
    for i in "${!plugin_ids[@]}"; do
        if [[ ${selections[$i]} -eq 1 ]]; then
            SELECTED_PLUGINS="${SELECTED_PLUGINS}${plugin_ids[$i]},"
        fi
    done
    [[ -z "$SELECTED_PLUGINS" ]] && SELECTED_PLUGINS="none"

    # Print a clear confirmation so the user sees what was selected
    if [[ "$SELECTED_PLUGINS" == "none" ]]; then
        printf '  %bNo integrations selected.%b\n' "$DIM" "$RESET" >&2
    else
        # Build a human-readable list
        local human_list=""
        for i in "${!plugin_ids[@]}"; do
            if [[ ${selections[$i]} -eq 1 ]]; then
                local name="${options[$i]%% (*}"
                human_list="${human_list}${human_list:+, }${name}"
            fi
        done
        printf '  %b→ Selected:%b %s\n' "$GREEN" "$RESET" "$human_list" >&2
    fi
}

# Draw the TUI menu (first draw — no cursor movement needed)
_draw_tui_menu() {
    local cursor=$1
    local n=$2
    shift 2
    # Remaining args: option_0 option_1 option_2 sel_0 sel_1 sel_2
    local opts=()
    local sels=()
    local i
    for ((i = 0; i < n; i++)); do
        opts+=("$1"); shift
    done
    for ((i = 0; i < n; i++)); do
        sels+=("$1"); shift
    done
    for ((i = 0; i < n; i++)); do
        local prefix="    "
        local box="[ ]"
        if [[ $i -eq $cursor ]]; then
            prefix="  ${CYAN}${BOLD}>${RESET} "
        fi
        if [[ ${sels[$i]} -eq 1 ]]; then
            box="[${GREEN}x${RESET}]"
        fi
        printf '\r\033[K%s %b%s%b\n' "$prefix" "$BOLD" "${opts[$i]}" "$RESET"
    done
}

# Redraw: move cursor up n lines first, then draw
_redraw_tui_menu() {
    local cursor=$1
    local n=$2
    # Move cursor up n lines
    printf '\033[%dA' "$n"
    # Now draw (same as _draw_tui_menu)
    _draw_tui_menu "$cursor" "$n" "${@:3}"
}

# Numbered fallback menu (for non-TTY or --no-menu)
_select_plugins_numbered() {
    local opts=()
    local ids=()
    local descs=()
    # Parse args: first 3 are options, next 3 are ids, next 3 are descriptions
    # (We know there are exactly 3 plugins — hardcoded for simplicity.)
    local i
    for ((i = 0; i < 3; i++)); do
        opts+=("$1"); shift
    done
    for ((i = 0; i < 3; i++)); do
        ids+=("$1"); shift
    done
    for ((i = 0; i < 3; i++)); do
        descs+=("$1"); shift
    done

    # NOTE: do NOT redirect read to /dev/tty here — that breaks when stdin is
    # piped (e.g. `echo "1,3" | bash install.sh --no-menu`). Just read from
    # whatever stdin is. The caller already verified we're in the fallback path.
    printf '  Select integrations to install (comma-separated, e.g. 1,3):\n' >&2
    for ((i = 0; i < 3; i++)); do
        printf '  %d) %s — %s\n' "$((i+1))" "${opts[$i]}" "${descs[$i]}" >&2
    done
    echo  "  4) Skip" >&2
    echo  "" >&2
    local choice=""
    read -r -p "  Select (1-4) [default: 1]: " choice || choice="1"
    choice="${choice:-1}"

    SELECTED_PLUGINS=""
    # Support comma-separated input like "1,3"
    local picked=()
    IFS=',' read -ra picked <<< "$choice"
    local p
    for p in "${picked[@]}"; do
        p="${p// /}"  # trim spaces
        case "$p" in
            1) SELECTED_PLUGINS="${SELECTED_PLUGINS}${ids[0]}," ;;
            2) SELECTED_PLUGINS="${SELECTED_PLUGINS}${ids[1]}," ;;
            3) SELECTED_PLUGINS="${SELECTED_PLUGINS}${ids[2]}," ;;
            4|skip|none) ;;
            *) ;;
        esac
    done
    [[ -z "$SELECTED_PLUGINS" ]] && SELECTED_PLUGINS="none"

    # Confirmation
    if [[ "$SELECTED_PLUGINS" == "none" ]]; then
        printf '  %bNo integrations selected.%b\n' "$DIM" "$RESET" >&2
    else
        local human_list=""
        for ((i = 0; i < 3; i++)); do
            if [[ "$SELECTED_PLUGINS" == *"${ids[$i]}"* ]]; then
                local name="${opts[$i]%% (*}"
                human_list="${human_list}${human_list:+, }${name}"
            fi
        done
        printf '  %b→ Selected:%b %s\n' "$GREEN" "$RESET" "$human_list" >&2
    fi
}

# ── Run plugin selection if needed ────────────────────────────────────────────
if [ -z "$SELECTED_PLUGINS" ] && [ "$NON_INTERACTIVE" = false ]; then
    echo ""
    divider
    printf "  %bAI Agent Integrations%b\n" "${BOLD}" "${RESET}"
    select_plugins_interactive
    divider
fi

# ── Antigravity plugin ───────────────────────────────────────────────────────
if [[ "$SELECTED_PLUGINS" == *"antigravity"* || "$SELECTED_PLUGINS" == *"all"* ]]; then
    AGY_PLUGINS_DIR="$HOME/.gemini/config/plugins/vcs-edit"
    mkdir -p "$AGY_PLUGINS_DIR"
    if [ -d "$INSTALL_DIR/.agy" ]; then
        cp -r "$INSTALL_DIR/.agy/"* "$AGY_PLUGINS_DIR/"
        ok "Antigravity plugin installed → $AGY_PLUGINS_DIR"
    else
        warn "Antigravity plugin source not found in $INSTALL_DIR/.agy"
    fi
fi

# ── Claude integration: just drop vcs-cli.md into ~/.claude/rules/ ───────────
# No hooks, no plugins. Claude Code automatically loads rules files from
# ~/.claude/rules/ as part of its system prompt — perfect for our use case.
if [[ "$SELECTED_PLUGINS" == *"claude"* || "$SELECTED_PLUGINS" == *"all"* ]]; then
    CLAUDE_RULES_DIR="$HOME/.claude/rules"
    mkdir -p "$CLAUDE_RULES_DIR"
    if [ -f "$INSTALL_DIR/.claude/vcs-cli.md" ]; then
        cp -f "$INSTALL_DIR/.claude/vcs-cli.md" "$CLAUDE_RULES_DIR/vcs-cli.md"
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
fi

# ── Codex integration: ~/.codex/AGENTS.md (with override logic) ──────────────
# Codex does not have a hooks/rules system. It loads ~/.codex/AGENTS.md at
# session start. We respect the user's hierarchy:
#   1. If ~/.codex/AGENTS.override.md exists  → do nothing (user has overridden)
#   2. Else if ~/.codex/AGENTS.md exists       → do nothing (user already has one)
#   3. Else                                    → create AGENTS.md with VCS instructions
if [[ "$SELECTED_PLUGINS" == *"codex"* || "$SELECTED_PLUGINS" == *"all"* ]]; then
    CODEX_DIR="$HOME/.codex"
    mkdir -p "$CODEX_DIR"

    OVERRIDE_FILE="$CODEX_DIR/AGENTS.override.md"
    AGENTS_FILE="$CODEX_DIR/AGENTS.md"

    if [ -f "$OVERRIDE_FILE" ]; then
        ok "Codex: AGENTS.override.md detected — respecting user override, no changes made."
    elif [ -f "$AGENTS_FILE" ]; then
        ok "Codex: AGENTS.md already exists — leaving it untouched (user has their own)."
        info "        To install VCS instructions, rename your file to AGENTS.override.md"
        info "        first, then re-run this installer."
    else
        # Neither exists → create one with VCS instructions
        if [ -f "$INSTALL_DIR/.codex/AGENTS.md" ]; then
            cp -f "$INSTALL_DIR/.codex/AGENTS.md" "$AGENTS_FILE"
            ok "Codex: created $AGENTS_FILE with VCS CLI instructions."
        else
            warn "Codex: source template not found at $INSTALL_DIR/.codex/AGENTS.md"
        fi
    fi
fi

# ── Complete ──────────────────────────────────────────────────────────────────
echo ""
printf '%b\n' "${GREEN}${BOLD}Installation Complete.${RESET}"
divider
info "Try running: ${BOLD}vcs --help${RESET}"
echo ""
