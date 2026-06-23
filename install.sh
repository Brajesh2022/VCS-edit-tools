#!/usr/bin/env bash

set -e

# Default installation directories
INSTALL_DIR="$HOME/.VCS-edit-tools"
BIN_DIR="$HOME/.local/bin"

# Detect OS
OS="$(uname -s)"
case "${OS}" in
    Linux*)     MACHINE=Linux;;
    Darwin*)    MACHINE=Mac;;
    CYGWIN*)    MACHINE=Cygwin;;
    MINGW*)     MACHINE=MinGw;;
    *)          MACHINE="UNKNOWN:${OS}"
esac

# Termux environment check
if [[ -n "${PREFIX:-}" && "${PREFIX:-}" == *"/usr"* && "${OS:-}" == "Linux" ]]; then
    BIN_DIR="${PREFIX}/bin"
    echo "Detected Termux environment."
fi

# Parse arguments for non-interactive installation
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
            # backward compatibility
            SELECTED_PLUGINS="antigravity"
            ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

echo "Installing VCS Edit CLI..."

# Check dependencies
deps_missing=false
for cmd in git python3; do
    if ! command -v $cmd &> /dev/null; then
        echo "Error: $cmd is required but not installed."
        deps_missing=true
    fi
done

if [ "$deps_missing" = true ]; then
    if [ "$NON_INTERACTIVE" = true ]; then
        echo "Non-interactive mode: please install the missing dependencies manually."
        exit 1
    fi
    echo ""
    read -p "Do you want to automatically install missing dependencies? (y/n) " -n 1 -r < /dev/tty
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        if command -v pkg &> /dev/null; then
            echo "Using pkg to install dependencies..."
            pkg install -y git python
        elif command -v apt &> /dev/null; then
            echo "Using apt to install dependencies..."
            sudo apt update && sudo apt install -y git python3
        elif command -v brew &> /dev/null; then
            echo "Using brew to install dependencies..."
            brew install git python3
        else
            echo "Could not detect package manager. Please install git and python3 manually."
            exit 1
        fi
    else
        echo "Cannot proceed without dependencies. Exiting."
        exit 1
    fi
fi

# Clone or update repository
if [ -d "$INSTALL_DIR" ]; then
    echo "Updating existing installation in $INSTALL_DIR..."
    git -C "$INSTALL_DIR" pull --quiet origin main || git -C "$INSTALL_DIR" pull --quiet origin master
else
    echo "Cloning repository to $INSTALL_DIR..."
    git clone --quiet https://github.com/Brajesh2022/VCS-edit-tools.git "$INSTALL_DIR"
fi

# Setup bin directory
mkdir -p "$BIN_DIR"

# Make the CLI executable and create symlink
chmod +x "$INSTALL_DIR/vcs"

# Fix shebang for Termux environment if applicable
if command -v termux-fix-shebang &> /dev/null; then
    termux-fix-shebang "$INSTALL_DIR/vcs"
fi

ln -sf "$INSTALL_DIR/vcs" "$BIN_DIR/vcs"

echo "VCS Edit CLI installed successfully to $BIN_DIR/vcs"

# Check if BIN_DIR is in PATH
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo "Warning: $BIN_DIR is not in your PATH."
    echo "Please add 'export PATH=\"\$PATH:$BIN_DIR\"' to your ~/.bashrc, ~/.zshrc, or equivalent."
fi

# Plugin Installation
if [ -z "$SELECTED_PLUGINS" ] && [ "$NON_INTERACTIVE" = false ]; then
    echo ""
    echo "=================================================="
    echo "          AI Agent Plugins Installation           "
    echo "=================================================="
    echo "Which AI tool do you want to install plugins for?"
    echo "1) Antigravity (.agy)"
    echo "2) Skip plugin installation"
    echo "=================================================="
    read -p "Select an option (1-2) [default: 1]: " choice < /dev/tty
    
    case "${choice:-1}" in
        1) SELECTED_PLUGINS="antigravity" ;;
        2) SELECTED_PLUGINS="none" ;;
        *) SELECTED_PLUGINS="none" ;;
    esac
fi

if [[ "$SELECTED_PLUGINS" == *"antigravity"* || "$SELECTED_PLUGINS" == *"all"* ]]; then
    echo "Installing Antigravity plugin..."
    AGY_PLUGINS_DIR="$HOME/.gemini/config/plugins/vcs-edit"
    mkdir -p "$AGY_PLUGINS_DIR"
    if [ -d "$INSTALL_DIR/.agy" ]; then
        cp -r "$INSTALL_DIR/.agy/"* "$AGY_PLUGINS_DIR/"
        echo "Antigravity plugin installed to $AGY_PLUGINS_DIR"
    else
        echo "Warning: Antigravity plugin source not found in $INSTALL_DIR/.agy"
    fi
fi

echo ""
echo "Installation complete! Try running 'vcs --help' (you may need to restart your terminal or source your profile first)."
