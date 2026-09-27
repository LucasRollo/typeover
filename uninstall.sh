#!/usr/bin/env bash
# Removes TypeOver. Leaves iTerm's Python API setting alone, since other
# scripts may use it. Turn it off under Settings → General → Magic if you like.
set -euo pipefail
DEST="$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch/typeover.py"
if [ -f "$DEST" ]; then
  rm -f "$DEST"
  echo "Removed TypeOver. Restart iTerm to stop the running copy."
else
  echo "TypeOver isn't installed."
fi
