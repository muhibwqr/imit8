#!/usr/bin/env bash
# One-command install for macOS (and Linux):
#
#   curl -fsSL https://raw.githubusercontent.com/muhibwqr/imit8/main/install.sh | bash
#
# Clones imit8 into ~/.imit8/src, installs it into its own virtualenv, symlinks the
# `imit8` command onto your PATH, and on macOS builds Imit8.app for the dock.
set -euo pipefail

REPO="${IMIT8_REPO:-https://github.com/muhibwqr/imit8}"
SRC="${IMIT8_SRC:-$HOME/.imit8/src}"
VENV="${IMIT8_VENV:-$HOME/.imit8/venv}"
BIN_DIR="${IMIT8_BIN_DIR:-$HOME/.local/bin}"

say() { printf '\033[1;35mimit8\033[0m %s\n' "$1"; }
die() { printf '\033[1;31mimit8\033[0m %s\n' "$1" >&2; exit 1; }

command -v git >/dev/null || die "git is required (xcode-select --install)"
PYTHON_BIN="$(command -v python3.12 || command -v python3.11 || command -v python3 || true)"
[[ -n "$PYTHON_BIN" ]] || die "python3 is required (brew install python@3.12)"
"$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || die "python 3.10+ is required, found $($PYTHON_BIN -V)"

if [[ -d "$SRC/.git" ]]; then
  say "updating $SRC"
  git -C "$SRC" pull --ff-only
else
  say "cloning into $SRC"
  mkdir -p "$(dirname "$SRC")"
  git clone --depth 1 "$REPO" "$SRC"
fi

say "installing into $VENV"
"$PYTHON_BIN" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet "$SRC"

mkdir -p "$BIN_DIR"
ln -sf "$VENV/bin/imit8" "$BIN_DIR/imit8"
say "linked $BIN_DIR/imit8"

if [[ "$(uname -s)" == "Darwin" ]]; then
  PATH="$VENV/bin:$PATH" bash "$SRC/scripts/install_macos_app.sh"
else
  mkdir -p "$HOME/.local/share/applications"
  cp "$SRC/scripts/imit8.desktop" "$HOME/.local/share/applications/"
  say "installed the desktop entry"
fi

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) say "add this to your shell profile:  export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac

cat <<'NEXT'

next:
  export OPENROUTER_API_KEY=sk-or-...     # https://openrouter.ai/settings/keys
  imit8                                    # the wyd? spotlight
  imit8 mcp                                # MCP tools for another agent session

NEXT
