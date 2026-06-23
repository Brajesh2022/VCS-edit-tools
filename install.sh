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
if [[ -n "$PREFIX" && "$PREFIX" == *"/usr"* && "$OS" == "Linux" ]]; then
    BIN_DIR="$PREFIX/bin"
    echo "Detected Termux environment."
fi

# Parse arguments for non-interactive installation
NON_INTERACTIVE=false
INSTALL_PLUGINS=false
while [[ "$#" -gt 0 ]]; do
    case $1 in
        -y|--yes) NON_INTERACTIVE=true ;;
        --install-plugins) INSTALL_PLUGINS=true ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

echo "Installing VCS Edit CLI..."

# Check dependencies
if ! command -v git &> /dev/null; then
    echo "Error: git is required but not installed."
    exit 1
fi

if ! command -v python3 &> /dev/null; then
    echo "Error: python3 is required but not installed."
    exit 1
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
if [[ -n "$PREFIX" && "$PREFIX" == *"/usr"* && "$OS" == "Linux" ]]; then
    if command -v termux-fix-shebang &> /dev/null; then
        termux-fix-shebang "$INSTALL_DIR/vcs"
    fi
fi

ln -sf "$INSTALL_DIR/vcs" "$BIN_DIR/vcs"

echo "VCS Edit CLI installed successfully to $BIN_DIR/vcs"

# Check if BIN_DIR is in PATH
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo "Warning: $BIN_DIR is not in your PATH."
    echo "Please add 'export PATH=\"\$PATH:$BIN_DIR\"' to your ~/.bashrc, ~/.zshrc, or equivalent."
fi

# Plugin Installation
if [ "$NON_INTERACTIVE" = true ]; then
    if [ "$INSTALL_PLUGINS" = true ]; then
        do_install_plugins=true
    else
        do_install_plugins=false
    fi
else
    echo ""
    read -p "Do you want to install AI Agent plugins for VCS Edit? (y/n) " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        do_install_plugins=true
    else
        do_install_plugins=false
    fi
fi

if [ "$do_install_plugins" = true ]; then
    echo "Available plugins:"
    echo "1) Antigravity (.agy)"
    
    if [ "$NON_INTERACTIVE" = true ]; then
        choices="1"
    else
        read -p "Enter choices (e.g. 1): " choices
    fi
    
    if [[ "$choices" == *"1"* ]]; then
        echo "Installing Antigravity plugin..."
        AGY_PLUGINS_DIR="$HOME/.gemini/config/plugins/vcs-edit"
        mkdir -p "$AGY_PLUGINS_DIR"
        cp -r "$INSTALL_DIR/.agy/"* "$AGY_PLUGINS_DIR/"
        echo "Antigravity plugin installed to $AGY_PLUGINS_DIR"
    fi
    # Future tools like .claude can be added here
fi

echo ""
echo "Installation complete! Try running 'vcs --help' (you may need to restart your terminal or source your profile first)."
