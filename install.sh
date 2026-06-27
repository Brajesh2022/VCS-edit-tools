#!/usr/bin/env bash
# VCS Edit CLI - Installer (v2 — redesigned interactive menu)
#
# Robust cross-platform installer with:
#   * Arrow-key + SPACE + ENTER TUI plugin picker (real TTY required)
#   * Numbered fallback when TTY is unavailable
#   * Headless mode (-y / --plugins) for CI/CD
#   * Proper terminal cleanup trap (cursor always restored, even on Ctrl-C)
#   * Works on Linux, macOS, Termux, Cygwin, MinGW
set -Eeuo pipefail

# ============================================================================
# Terminal state — track cursor visibility so we ALWAYS restore it
# ============================================================================
_CURSOR_HIDDEN=0

_term_cleanup() {
    if [[ $_CURSOR_HIDDEN -ne 0 ]]; then
        printf '\033[?25h' >&2  # show cursor
        _CURSOR_HIDDEN=0
    fi
}
trap '_term_cleanup' EXIT
trap '_term_cleanup; exit 130' INT
trap '_term_cleanup; exit 143' TERM

# ============================================================================
# Colors — only when stdout is a TTY
# ============================================================================
if [[ -t 1 ]]; then
    BOLD=$'\033[1m';   DIM=$'\033[2m'
    GREEN=$'\033[32m'; RED=$'\033[31m'; CYAN=$'\033[36m'; YELLOW=$'\033[33m'
    RESET=$'\033[0m'
else
    BOLD=""; DIM=""; GREEN=""; RED=""; CYAN=""; YELLOW=""; RESET=""
fi

# ============================================================================
# Logging helpers
# ============================================================================
info()    { printf '%b\n' " ${CYAN}[..]${RESET} ${DIM}$*${RESET}"; }
ok()      { printf '%b\n' " ${GREEN}[OK]${RESET} $*"; }
warn()    { printf '%b\n' " ${YELLOW}[!]${RESET} $*"; }
die()     {
    _term_cleanup
    printf '\n%b\n' " ${RED}[ERR]${RESET} ${1:-Installation failed.}" >&2
    exit 1
}
divider() { printf '%b\n' "${DIM}────────────────────────────────────────${RESET}"; }

# ============================================================================
# Spinner — runs a backgrounded command with a rotating glyph
#   spinner "<message>" <command> [args...]
# ============================================================================
spinner() {
    local msg="$1"; shift
    local spinstr='|/-\'
    local i=0 pid rc

    "$@" >/dev/null 2>&1 &
    pid=$!
    _CURSOR_HIDDEN=1
    printf '\033[?25l' >&2          # hide cursor
    while kill -0 "$pid" 2>/dev/null; do
        printf '\r\033[K %b[%c]%b %b%s%b' \
            "$CYAN" "${spinstr:i++%4:1}" "$RESET" "$DIM" "$msg" "$RESET" >&2
        sleep 0.1
    done
    rc=0; wait "$pid" || rc=$?
    _CURSOR_HIDDEN=0
    printf '\033[?25h' >&2          # show cursor
    if [[ $rc -eq 0 ]]; then
        printf '\r\033[K %b[OK]%b %s\n' "$GREEN" "$RESET" "$msg"
    else
        printf '\r\033[K %b[ERR]%b %s\n' "$RED" "$RESET" "$msg"
    fi
    return $rc
}

# ============================================================================
# Header
# ============================================================================
echo ""
printf "  %bVCS Edit Tools%b\n" "${BOLD}${CYAN}" "${RESET}"
printf "  %bUniversal Installer%b\n" "${DIM}" "${RESET}"
echo ""
divider

# ============================================================================
# Initialization & flag parsing
# ============================================================================
INSTALL_DIR="$HOME/.VCS-edit-tools"
BIN_DIR="$HOME/.local/bin"

OS="$(uname -s)"
case "$OS" in
    Linux*)  MACHINE=Linux ;;
    Darwin*) MACHINE=Mac ;;
    CYGWIN*) MACHINE=Cygwin ;;
    MINGW*)  MACHINE=MinGw ;;
    *)       MACHINE="UNKNOWN:$OS" ;;
esac

if [[ -n "${PREFIX:-}" && "${PREFIX:-}" == *"/usr"* && "${OS:-}" == "Linux" ]]; then
    BIN_DIR="${PREFIX}/bin"
    ok "Environment: Termux"
else
    ok "Environment: $MACHINE"
fi

NON_INTERACTIVE=false
SELECTED_PLUGINS=""

usage() {
    cat <<EOF
VCS Edit Tools installer

Usage: install.sh [options]

Options:
  -y, --yes               Non-interactive (skip all prompts)
  --plugins LIST          Comma-separated: antigravity,claude,codex,all
  --install-plugins       Shortcut for --plugins antigravity
  -h, --help              Show this help

Examples:
  install.sh -y --plugins claude,codex
  install.sh              # interactive TUI
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -y|--yes) NON_INTERACTIVE=true ;;
        --plugins)
            [[ $# -lt 2 ]] && die "--plugins requires a value"
            SELECTED_PLUGINS="$2"
            shift
            ;;
        --install-plugins) SELECTED_PLUGINS="antigravity" ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown parameter: $1" ;;
    esac
    shift
done

# ============================================================================
# Dependencies
# ============================================================================
deps_missing=()
for cmd in git python3; do
    command -v "$cmd" >/dev/null 2>&1 || deps_missing+=("$cmd")
done

if [[ ${#deps_missing[@]} -gt 0 ]]; then
    if [[ "$NON_INTERACTIVE" == true ]]; then
        die "Non-interactive mode: missing deps (${deps_missing[*]}). Install manually."
    fi
    printf '\n'
    warn "Missing dependencies: ${deps_missing[*]}"
    # Reattach stdin to TTY if piped (curl|bash case)
    if [[ ! -t 0 ]] && [[ -c /dev/tty ]]; then
        exec 0</dev/tty || true
    fi
    choice=""
    if [[ -t 0 ]]; then
        read -r -p "  Install missing dependencies automatically? (y/n) " choice || choice="n"
    else
        choice="n"
    fi
    if [[ "$choice" =~ ^[Yy]$ ]]; then
        if command -v pkg >/dev/null 2>&1; then
            spinner "Installing via pkg..." pkg install -y git python
        elif command -v apt >/dev/null 2>&1; then
            spinner "Installing via apt..." bash -c 'sudo apt update && sudo apt install -y git python3'
        elif command -v brew >/dev/null 2>&1; then
            spinner "Installing via brew..." brew install git python3
        else
            die "No supported package manager. Install deps manually."
        fi
    else
        die "Dependencies required."
    fi
fi
ok "Dependencies met"

# ============================================================================
# Clone / Update repo
# ============================================================================
if [[ -d "$INSTALL_DIR" ]]; then
    spinner "Updating repository..." \
        bash -c "git -C '$INSTALL_DIR' fetch origin main && git -C '$INSTALL_DIR' reset --hard origin/main || git -C '$INSTALL_DIR' pull origin master"
else
    spinner "Cloning repository..." \
        git clone https://github.com/Brajesh2022/VCS-edit-tools.git "$INSTALL_DIR"
fi

# ============================================================================
# Install — link vcs binary
# ============================================================================
mkdir -p "$BIN_DIR"
chmod +x "$INSTALL_DIR/vcs"
if command -v termux-fix-shebang >/dev/null 2>&1; then
    termux-fix-shebang "$INSTALL_DIR/vcs"
fi
ln -sf "$INSTALL_DIR/vcs" "$BIN_DIR/vcs"
ok "CLI linked to $BIN_DIR/vcs"

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    warn "$BIN_DIR is not in PATH."
    warn "Add this to your shell profile:"
    printf '  %bexport PATH="$PATH:%s"%b\n' "$CYAN" "$BIN_DIR" "$RESET"
fi

# ============================================================================
# Plugin installers
# ============================================================================
install_antigravity() {
    local AGY_PLUGINS_DIR="$HOME/.gemini/config/plugins/vcs-edit"
    mkdir -p "$AGY_PLUGINS_DIR"
    if [[ -d "$INSTALL_DIR/.agy" ]]; then
        cp -r "$INSTALL_DIR/.agy/." "$AGY_PLUGINS_DIR/" 2>/dev/null || true
        chmod +x "$AGY_PLUGINS_DIR/message.sh" 2>/dev/null || true
        ok "Antigravity plugin installed"
    else
        warn "Antigravity source not found at $INSTALL_DIR/.agy"
    fi
}

install_claude() {
    local CLAUDE_RULES_DIR="$HOME/.claude/rules"
    mkdir -p "$CLAUDE_RULES_DIR"
    if [[ -f "$INSTALL_DIR/.claude/vcs-cli.md" ]]; then
        cp -f "$INSTALL_DIR/.claude/vcs-cli.md" "$CLAUDE_RULES_DIR/vcs-cli.md"
        ok "Claude rules installed at $CLAUDE_RULES_DIR/vcs-cli.md"

        # Clean up legacy v1 hooks/plugins
        local LEGACY_PLUGIN_DIR="$HOME/.claude/plugins/vcs-edit"
        if [[ -d "$LEGACY_PLUGIN_DIR" ]]; then
            rm -rf "$LEGACY_PLUGIN_DIR"
            info "Removed legacy Claude hooks/plugins from $LEGACY_PLUGIN_DIR"
        fi

        # Remove legacy UserPromptSubmit hook from settings.json
        if [[ -f "$HOME/.claude/settings.json" ]]; then
            python3 - <<'PY' || true
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
PY
        fi
    else
        warn "Claude source not found at $INSTALL_DIR/.claude/vcs-cli.md"
    fi
}

install_codex() {
    local CODEX_DIR="$HOME/.codex"
    mkdir -p "$CODEX_DIR"
    local OVERRIDE_FILE="$CODEX_DIR/AGENTS.override.md"
    local AGENTS_FILE="$CODEX_DIR/AGENTS.md"

    if [[ -f "$OVERRIDE_FILE" ]]; then
        ok "Codex: AGENTS.override.md detected — respecting user override, no changes made."
    elif [[ -f "$AGENTS_FILE" ]]; then
        ok "Codex: AGENTS.md already exists — leaving it untouched."
        info "        To install VCS instructions, rename it to AGENTS.override.md first, then re-run."
    else
        if [[ -f "$INSTALL_DIR/.codex/AGENTS.md" ]]; then
            cp -f "$INSTALL_DIR/.codex/AGENTS.md" "$AGENTS_FILE"
            ok "Codex: created $AGENTS_FILE with VCS CLI instructions."
        else
            warn "Codex: source template not found at $INSTALL_DIR/.codex/AGENTS.md"
        fi
    fi
}

# ============================================================================
# Interactive plugin selection
# ============================================================================
menu_options=("Antigravity (.agy)" "Claude (.claude/rules/)" "Codex (~/.codex/AGENTS.md)")
menu_ids=("antigravity" "claude" "codex")

# Read a single keypress and emit a normalized token.
# Tokens: up | down | left | right | space | enter | esc | quit | <other-char>
read_key() {
    local k=""
    # Read one byte. On EOF (Ctrl-D), return 'esc' so the menu cancels safely.
    IFS= read -rsn1 k 2>/dev/null || { printf 'esc'; return 0; }

    # ESC — possibly the start of an arrow-key sequence
    if [[ "$k" == $'\e' ]]; then
        local k1="" k2=""
        # Generous 0.5s timeout — works on slow SSH / WSL terminals
        IFS= read -rsn1 -t 0.5 k1 2>/dev/null || true
        if [[ "$k1" == "[" || "$k1" == "O" ]]; then
            IFS= read -rsn1 -t 0.5 k2 2>/dev/null || true
            case "$k2" in
                A) printf 'up'    ;;
                B) printf 'down'  ;;
                C) printf 'right' ;;
                D) printf 'left'  ;;
                *) printf 'esc'   ;;
            esac
        elif [[ -z "$k1" ]]; then
            # Bare ESC — treat as cancel
            printf 'esc'
        else
            printf 'esc'
        fi
        return 0
    fi

    # Empty -> Enter (newline was the read terminator)
    if [[ -z "$k" ]]; then
        printf 'enter'; return 0
    fi
    # Space
    if [[ "$k" == " " ]]; then
        printf 'space'; return 0
    fi
    # q / Q = quick quit
    if [[ "$k" == "q" || "$k" == "Q" ]]; then
        printf 'quit'; return 0
    fi
    # Numeric shortcut
    if [[ "$k" =~ [0-9] ]]; then
        printf '%s' "$k"; return 0
    fi
    # Anything else: emit raw
    printf '%s' "$k"
}

# Draw the menu. Argument $1 = number of lines to move up before redrawing (0 = first draw).
draw_menu() {
    local move_up="${1:-0}"
    if [[ "$move_up" -gt 0 ]]; then
        printf '\033[%dA' "$move_up" >&2
    fi
    local i
    for i in "${!menu_options[@]}"; do
        if [[ $i -eq $cursor ]]; then
            local prefix="  ${CYAN}${BOLD}> "
        else
            local prefix="    "
        fi
        if [[ ${selections[$i]} -eq 1 ]]; then
            local box="[x]"
        else
            local box="[ ]"
        fi
        printf '\r\033[K%b%s %s%b\n' "$prefix" "$box" "${menu_options[$i]}" "$RESET" >&2
    done
}

if [[ -z "$SELECTED_PLUGINS" && "$NON_INTERACTIVE" == false ]]; then
    echo ""
    divider
    printf "  %bAI Agent Integrations%b\n" "${BOLD}" "${RESET}"

    selections=(0 0 0)
    cursor=0

    # Decide whether we have an interactive TTY
    have_tty=false
    if [[ -t 0 ]]; then
        have_tty=true
    elif [[ -c /dev/tty ]]; then
        # curl|bash case — try to reattach stdin to /dev/tty
        if exec 0</dev/tty 2>/dev/null; then
            [[ -t 0 ]] && have_tty=true
        fi
    fi

    if $have_tty; then
        # ─── TUI menu ─────────────────────────────────────────────────────
        echo "  ${DIM}(↑/↓ move · SPACE toggle · ENTER confirm · ESC/q skip)${RESET}"

        _CURSOR_HIDDEN=1
        printf '\033[?25l' >&2          # hide cursor
        menu_lines=${#menu_options[@]}
        draw_menu 0

        while true; do
            key=""
            # `|| true` — read_key always returns 0, but be defensive against set -e
            key="$(read_key)" || key=""

            case "$key" in
                up|left)
                    cursor=$(( (cursor - 1 + menu_lines) % menu_lines ))
                    draw_menu "$menu_lines"
                    ;;
                down|right)
                    cursor=$(( (cursor + 1) % menu_lines ))
                    draw_menu "$menu_lines"
                    ;;
                space)
                    if [[ ${selections[$cursor]} -eq 1 ]]; then
                        selections[$cursor]=0
                    else
                        selections[$cursor]=1
                    fi
                    draw_menu "$menu_lines"
                    ;;
                enter)
                    break
                    ;;
                esc|quit)
                    # Cancel — clear all selections
                    selections=(0 0 0)
                    break
                    ;;
                1)
                    cursor=0
                    selections[0]=$(( 1 - selections[0] ))
                    draw_menu "$menu_lines"
                    ;;
                2)
                    cursor=1
                    selections[1]=$(( 1 - selections[1] ))
                    draw_menu "$menu_lines"
                    ;;
                3)
                    cursor=2
                    selections[2]=$(( 1 - selections[2] ))
                    draw_menu "$menu_lines"
                    ;;
                *)
                    # Unknown key — redraw to give visual feedback that we're alive
                    draw_menu "$menu_lines"
                    ;;
            esac
        done

        _CURSOR_HIDDEN=0
        printf '\033[?25h\n' >&2        # show cursor + blank line

        SELECTED_PLUGINS=""
        for i in "${!menu_options[@]}"; do
            if [[ ${selections[$i]} -eq 1 ]]; then
                SELECTED_PLUGINS="${SELECTED_PLUGINS}${menu_ids[$i]},"
            fi
        done
        [[ -z "$SELECTED_PLUGINS" ]] && SELECTED_PLUGINS="none"

    else
        # ─── Numbered fallback (no TTY) ──────────────────────────────────
        cat <<EOF
  Select integrations to install:
    1) Antigravity (.agy)
    2) Claude    (.claude/rules/)
    3) Codex     (~/.codex/AGENTS.md)
    4) All of the above
    5) Skip
EOF
        choice=""
        read -r -p "  Choice [1-5, default 5]: " choice || choice="5"
        case "${choice:-5}" in
            1) SELECTED_PLUGINS="antigravity" ;;
            2) SELECTED_PLUGINS="claude" ;;
            3) SELECTED_PLUGINS="codex" ;;
            4) SELECTED_PLUGINS="all" ;;
            *) SELECTED_PLUGINS="none" ;;
        esac
    fi
    divider
fi

# ============================================================================
# Apply plugin selections
# ============================================================================
if [[ "$SELECTED_PLUGINS" == "all" || "$SELECTED_PLUGINS" == *"antigravity"* ]]; then
    install_antigravity
fi
if [[ "$SELECTED_PLUGINS" == "all" || "$SELECTED_PLUGINS" == *"claude"* ]]; then
    install_claude
fi
if [[ "$SELECTED_PLUGINS" == "all" || "$SELECTED_PLUGINS" == *"codex"* ]]; then
    install_codex
fi
if [[ "$SELECTED_PLUGINS" == "none" ]]; then
    info "No plugins selected — CLI still installed and ready to use."
fi

# ============================================================================
# Done
# ============================================================================
echo ""
printf '%b\n' "${GREEN}${BOLD}Installation Complete.${RESET}"
divider
info "Try running: ${BOLD}vcs --help${RESET}"
echo ""
