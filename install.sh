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
    (git -C "$INSTALL_DIR" pull origin main || git -C "$INSTALL_DIR" pull origin master) >/dev/null 2>&1 &
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
    printf "  %bAI Agent Plugins%b\n" "${BOLD}" "${RESET}"
    
    options=("Antigravity (.agy)" "Skip")
    selected=0
    
    # Try to reassign stdin to tty for interactive input if piped
    if [ ! -t 0 ] && [ -c /dev/tty ]; then
        exec < /dev/tty || true
    fi
    
    if [ -t 0 ]; then
        printf "\033[?25l" # Hide cursor
        while true; do
            for i in "${!options[@]}"; do
                if [[ $i -eq $selected ]]; then
                    printf "\r\033[K  %b> ◉ %s%b\n" "${CYAN}${BOLD}" "${options[$i]}" "${RESET}"
                else
                    printf "\r\033[K    ◯ %s\n" "${options[$i]}"
                fi
            done
            
            key=""
            # Read 1 char. If it fails, fallback to break
            if ! read -rsn1 key; then
                break
            fi
            
            case "$key" in
                $'\x1b')
                    read -rsn2 key || true
                    if [[ "$key" == "[A" || "$key" == "[D" ]]; then
                        ((selected--)); [[ $selected -lt 0 ]] && selected=$((${#options[@]} - 1))
                    elif [[ "$key" == "[B" || "$key" == "[C" ]]; then
                        ((selected++)); [[ $selected -ge ${#options[@]} ]] && selected=0
                    fi
                    ;;
                "") break ;;
            esac
            printf "\033[%dA" "${#options[@]}"
        done
        printf "\033[?25h\n" # Restore cursor
    else
        # Fallback if no TTY is detected
        echo "  1) Antigravity (.agy)"
        echo "  2) Skip"
        echo ""
        read -p "  Select an option (1-2) [default: 1]: " choice || choice="1"
        case "${choice:-1}" in
            1) selected=0 ;;
            2) selected=1 ;;
            *) selected=1 ;;
        esac
    fi
    divider
    
    if [[ $selected -eq 0 ]]; then
        SELECTED_PLUGINS="antigravity"
    else
        SELECTED_PLUGINS="none"
    fi
fi
    
    if [[ $selected -eq 0 ]]; then
        SELECTED_PLUGINS="antigravity"
    else
        SELECTED_PLUGINS="none"
    fi
fi

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

# ── Complete ──────────────────────────────────────────────────────────────────
echo ""
printf '%b\n' "${GREEN}${BOLD}Installation Complete.${RESET}"
divider
info "Try running: ${BOLD}vcs --help${RESET}"
echo ""
