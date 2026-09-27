#!/usr/bin/env bash
# TypeOver installer for iTerm2.
#
#   curl -fsSL https://raw.githubusercontent.com/YOUR_GITHUB_USERNAME/typeover/main/install.sh | bash
#
# What it does:
#   1. Puts typeover.py in iTerm2's AutoLaunch folder (so it starts with iTerm).
#   2. Turns on iTerm2's Python API if it's off (needs an iTerm restart).
#   3. Starts the script.
set -euo pipefail

REPO="${TYPEOVER_REPO:-YOUR_GITHUB_USERNAME/typeover}"
BRANCH="${TYPEOVER_BRANCH:-main}"
RAW="https://raw.githubusercontent.com/${REPO}/${BRANCH}"
PREFS="com.googlecode.iterm2"
DEST_DIR="$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch"
DEST="$DEST_DIR/typeover.py"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
die()  { printf '\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }

# Read answers from the keyboard even when this script is piped from curl.
ask() {
  local prompt="$1" reply=""
  if { : </dev/tty; } 2>/dev/null; then
    read -r -p "$prompt" reply < /dev/tty || true
  else
    reply="n"                      # no keyboard: never restart iTerm unasked
  fi
  printf '%s' "$reply"
}

bold "Installing TypeOver for iTerm2"

# ---- checks -----------------------------------------------------------------
[ "$(uname)" = "Darwin" ] || die "TypeOver needs macOS and iTerm2."
if [ ! -d "/Applications/iTerm.app" ] && [ ! -d "$HOME/Applications/iTerm.app" ]; then
  die "iTerm2 isn't installed. Get it at https://iterm2.com"
fi

# ---- 1. install the script --------------------------------------------------
mkdir -p "$DEST_DIR"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/typeover.py" ]; then
  cp "$SCRIPT_DIR/typeover.py" "$DEST"                 # running from a clone
else
  TMP="$(mktemp)"
  curl -fsSL "$RAW/typeover.py" -o "$TMP" || die "Couldn't download typeover.py from $RAW"
  head -1 "$TMP" | grep -q python || die "Downloaded file doesn't look like the script. Check TYPEOVER_REPO."
  mv "$TMP" "$DEST"
fi
info "✓ Installed script to AutoLaunch"

# ---- 2 & 3. enable the Python API, then start the script ---------------------
api_on() { [ "$(defaults read "$PREFS" EnableAPIServer 2>/dev/null || echo 0)" = "1" ]; }
iterm_running() { pgrep -xq iTerm2; }

if api_on; then
  info "✓ Python API already on"
  if iterm_running; then
    osascript -e "tell application \"iTerm2\" to launch API script named \"$DEST\"" >/dev/null 2>&1 \
      && info "✓ Started TypeOver" \
      || info "• Start it from the menu bar: Scripts → AutoLaunch → typeover.py"
  fi
  echo
  bold "Done. Select text in your prompt and start typing."
  info "First run only: if iTerm asks to download its Python runtime, click OK."
  exit 0
fi

if ! iterm_running; then
  defaults write "$PREFS" EnableAPIServer -bool true
  info "✓ Turned on iTerm's Python API"
  echo
  bold "Done. Open iTerm. TypeOver starts on its own."
  info "First run only: if iTerm asks to download its Python runtime, click OK."
  exit 0
fi

# iTerm is running, probably the very window you're typing in. Its settings can
# only change while it's closed, so hand off to a background helper that waits
# for iTerm to quit, flips the setting, and reopens it.
echo
info "iTerm's Python API is off, and turning it on needs a quick iTerm restart."
REPLY="$(ask "  Restart iTerm now? Your open tabs will close. [Y/n] ")"
case "$REPLY" in
  [nN]*)
    echo
    bold "Almost done. To finish, either:"
    info "• Quit iTerm and run this installer again from Terminal.app, or"
    info "• Turn on iTerm2 → Settings → General → Magic → Enable Python API, then choose"
    info "  Scripts → AutoLaunch → typeover.py in the menu bar."
    exit 0 ;;
esac

HELPER="$(mktemp -t typeover-helper)"
cat > "$HELPER" <<EOF
#!/bin/bash
for i in \$(seq 1 600); do pgrep -xq iTerm2 || break; sleep 0.5; done
pgrep -xq iTerm2 && exit 1          # user cancelled the quit
sleep 1
defaults write $PREFS EnableAPIServer -bool true
open -a iTerm
rm -f "$HELPER"
EOF
chmod +x "$HELPER"
nohup "$HELPER" >/dev/null 2>&1 &
disown || true

bold "Restarting iTerm…"
info "When it reopens, TypeOver is running."
info "First run only: if iTerm asks to download its Python runtime, click OK."
sleep 1
osascript -e 'tell application "iTerm2" to quit' >/dev/null 2>&1 || true
