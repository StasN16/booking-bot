#!/usr/bin/env bash
#
# Put the bot online on Render.
#
#   ./scripts/go_online.sh                    start to finish
#   ./scripts/go_online.sh https://ADDRESS    only the end, for a server already up
#
# See scripts/go_online.py.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Homebrew's tools, Poetry among them, for a Terminal window older than the setup.
if ! command -v poetry >/dev/null 2>&1; then
  for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [ -x "$candidate" ]; then
      eval "$("$candidate" shellenv)"
      break
    fi
  done
fi
command -v poetry >/dev/null 2>&1 || { echo "Poetry is missing. Run ./scripts/setup_mac.sh first."; exit 1; }

exec poetry run python scripts/go_online.py "$@"
