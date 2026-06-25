#!/usr/bin/env bash
# VCS Edit CLI - Installer
set -Eeuo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  BOLD="\033[1m"
  DIM="\033[2m"
  GREEN="\033[32m"
  RED="\033[31m"
  CYAN="\033[36m"
  RESET="\033[0m"
else
  BOLD="" DIM="" GREEN="" RED="" CYAN="" RESET=""
fi

# ── Helpers ───────────────────────────────────────────────────────────────────
info()    { printf '%b\n' " ${CYAN}[..]${RESET} ${DIM}$*${RESET}"; }
ok()      { printf '%b\n' " ${GREEN}[OK]${RESET} $*"; }
die()     {
  printf "\033[?25h" >&2
  printf '\n%b\n' " ${RED}[ERR]${RESET} ${1:-Installation failed.}" >&2
  exit 1
}
warn()    { printf '%b\n' " ${RED}[!]${RESET} $*"; }
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

# ── Plugins ───────────────────────────────────────────────────────────────────
if [ -z "$SELECTED_PLUGINS" ] && [ "$NON_INTERACTIVE" = false ]; then
    echo ""
    divider
    printf "  %bAI Agent Integrations%b\n" "${BOLD}" "${RESET}"

    options=("Antigravity (.agy)" "Claude (.claude/rules/)" "Codex (~/.codex/AGENTS.md)")
    plugin_ids=("antigravity" "claude" "codex")
    selections=(1 0 0)
    cursor=0

    # Try to reassign stdin to tty for interactive input if piped
    if [ ! -t 0 ] && [ -c /dev/tty ]; then
        exec < /dev/tty || true
    fi

    if [ -t 0 ]; then
        printf "\033[?25l" # Hide cursor
        echo "  (Use arrow keys to move, SPACE to toggle, ENTER to confirm)"
        while true; do
            for i in "${!options[@]}"; do
                if [[ $i -eq $cursor ]]; then
                    prefix="${CYAN}${BOLD}  > "
                else
                    prefix="    "
                fi

                if [[ ${selections[$i]} -eq 1 ]]; then
                    box="[x]"
                else
                    box="[ ]"
                fi

                printf "\r\033[K%b%s %s%b\n" "$prefix" "$box" "${options[$i]}" "${RESET}"
            done

            key=""
            if ! IFS= read -rsn1 key; then
                break
            fi
            case "$key" in
                $'\e'|$'\x1b')
                    k1=""
                    k2=""
                    IFS= read -rsn1 -t 0.1 k1 || true
                    if [[ "$k1" == "[" || "$k1" == "O" ]]; then
                        IFS= read -rsn1 -t 0.1 k2 || true
                        if [[ "$k2" == "A" || "$k2" == "D" ]]; then
                            cursor=$(( (cursor - 1 + ${#options[@]}) % ${#options[@]} ))
                        elif [[ "$k2" == "B" || "$k2" == "C" ]]; then
                            cursor=$(( (cursor + 1) % ${#options[@]} ))
                        fi
                    fi
                    ;;
                " ")
                    if [[ ${selections[$cursor]} -eq 1 ]]; then
                        selections[$cursor]=0
                    else
                        selections[$cursor]=1
                    fi
                    ;;
                "" | $'\n' | $'\r')
                    break
                    ;;
            esac
            printf "\033[%dA" "${#options[@]}"
        done
        printf "\033[?25h\n" # Restore cursor

        SELECTED_PLUGINS=""
        for i in "${!options[@]}"; do
            if [[ ${selections[$i]} -eq 1 ]]; then
                SELECTED_PLUGINS="${SELECTED_PLUGINS}${plugin_ids[$i]},"
            fi
        done
        [[ -z "$SELECTED_PLUGINS" ]] && SELECTED_PLUGINS="none"

    else
        # Fallback if no TTY is detected
        echo "  1) Antigravity (.agy)"
        echo "  2) Claude (.claude/rules/)"
        echo "  3) Codex (~/.codex/AGENTS.md)"
        echo "  4) Skip"
        echo ""
        read -p "  Select an option (1-4) [default: 1]: " choice || choice="1"
        case "${choice:-1}" in
            1) SELECTED_PLUGINS="antigravity," ;;
            2) SELECTED_PLUGINS="claude," ;;
            3) SELECTED_PLUGINS="codex," ;;
            *) SELECTED_PLUGINS="none" ;;
        esac
    fi
    divider
fi

# ── Antigravity plugin ───────────────────────────────────────────────────────
if [[ "$SELECTED_PLUGINS" == *"antigravity"* || "$SELECTED_PLUGINS" == *"all"* ]]; then
    AGY_PLUGINS_DIR="$HOME/.gemini/config/plugins/vcs-edit"
    mkdir -p "$AGY_PLUGINS_DIR"
    if [ -d "$INSTALL_DIR/.agy" ]; then
        cp -r "$INSTALL_DIR/.agy/"* "$AGY_PLUGINS_DIR/"
        ok "Antigravity plugin installed"
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
        ok "Claude rules installed at $CLAUDE_RULES_DIR/vcs-cli.md"

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
