#!/usr/bin/env bash
#
# One-time setup of the booking bot on a Mac.
#
#   ./scripts/setup_mac.sh
#
# Installs what the bot needs through Homebrew, fills in .env, creates a local
# database and runs the tests. Safe to run again: finished steps are skipped
# and nothing already configured is overwritten.
#
# Written for the bash 3.2 that ships with macOS, so it avoids anything newer:
# no associative arrays, no ${var,,}, and no expanding an array that might be
# empty while `set -u` is on, which 3.2 treats as an unbound variable.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

bold=$'\033[1m'; green=$'\033[32m'; yellow=$'\033[33m'; red=$'\033[31m'; reset=$'\033[0m'
step() { printf '\n%s==> %s%s\n' "$bold" "$*" "$reset"; }
ok()   { printf '    %s✓%s %s\n' "$green" "$reset" "$*"; }
note() { printf '    %s!%s %s\n' "$yellow" "$reset" "$*"; }
die()  { printf '\n%s✗ %s%s\n' "$red" "$*" "$reset" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "This script is for macOS. See README.md for other systems."

mkdir -p "$ROOT/logs"

# --- Homebrew ------------------------------------------------------------------

find_brew() {
  if command -v brew >/dev/null 2>&1; then
    return 0
  fi
  # Installed but not on PATH yet, which is normal right after installing.
  local candidate
  for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [ -x "$candidate" ]; then
      eval "$("$candidate" shellenv)"
      return 0
    fi
  done
  return 1
}

step "Homebrew"
if find_brew; then
  ok "found"
else
  note "Installing Homebrew. It asks for your Mac password (typing shows nothing)."
  # The password is asked for here, and the installer then runs without its
  # "Press RETURN to continue" question: any key there other than return,
  # even a stray one, cancels the whole install without saying so.
  sudo -v || die "Homebrew needs your Mac password. Run this again and type it."
  NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
    || die "Homebrew did not install. The messages above say why."
  find_brew || die "Homebrew installed but cannot be found. Open a new Terminal window and run this again."
  ok "installed"
fi

# New Terminal windows need to find Homebrew too.
PROFILE="$HOME/.zprofile"
if ! grep -qs "brew shellenv" "$PROFILE"; then
  # The $(...) is meant literally: it runs each time a Terminal opens.
  # shellcheck disable=SC2016
  printf '\neval "$(%s shellenv)"\n' "$(command -v brew)" >> "$PROFILE"
  ok "added Homebrew to $PROFILE"
fi

# --- tools ---------------------------------------------------------------------

# Homebrew now asks "Do you want to proceed with the installation? [y/n]"
# before installing anything that brings dependencies, which is everything
# here. The setup has already said what it installs, so it does not ask.
export HOMEBREW_NO_ASK=1

# Fails (returns 1) rather than stopping, so the caller decides what a
# missing tool means.
install_if_missing() {
  # $1: Homebrew name   $2: what to call it
  if brew list "$1" >/dev/null 2>&1; then
    ok "$2"
    return 0
  fi
  printf '    installing %s ...\n' "$2"
  # A download cut off halfway usually gets through on another try.
  local attempt
  for attempt in 1 2 3; do
    if brew install "$1"; then
      ok "$2 installed"
      return 0
    fi
    if [ "$attempt" -lt 3 ]; then
      note "the download failed; trying again in 10 seconds ($attempt of 3)"
      sleep 10
    fi
  done
  return 1
}

OFFLINE_HINT="Check the internet connection and run this again. If it keeps failing, your network may block the download: connect to your phone's hotspot for the setup."

step "Tools"
install_if_missing python@3.12   "Python 3.12"   || die "Could not install Python 3.12. $OFFLINE_HINT"
install_if_missing poetry        "Poetry"        || die "Could not install Poetry. $OFFLINE_HINT"
install_if_missing postgresql@16 "PostgreSQL 16" || die "Could not install PostgreSQL 16. $OFFLINE_HINT"
# Only start.sh needs ngrok, so without it everything else still gets set up.
NGROK_MISSING=""
if ! install_if_missing ngrok "ngrok"; then
  NGROK_MISSING=1
  note "ngrok could not be downloaded; carrying on with everything else"
fi

PYTHON="$(brew --prefix python@3.12)/bin/python3.12"
PG_READY="$(brew --prefix postgresql@16)/bin/pg_isready"
[ -x "$PYTHON" ]   || die "Python 3.12 is not at $PYTHON"
[ -x "$PG_READY" ] || die "PostgreSQL tools are not at $(dirname "$PG_READY")"

# --- database server -----------------------------------------------------------

step "PostgreSQL"
if "$PG_READY" -q -h localhost; then
  ok "running"
else
  brew services start postgresql@16 >/dev/null
  waited=0
  until "$PG_READY" -q -h localhost; do
    waited=$((waited + 1))
    [ "$waited" -le 30 ] || die "PostgreSQL did not start. Try: brew services restart postgresql@16"
    sleep 1
  done
  ok "started; it will also start by itself whenever you log in"
fi

# --- Python packages -----------------------------------------------------------

step "Python packages"
poetry env use "$PYTHON" >/dev/null
poetry install --no-interaction
ok "installed"

# --- configuration -------------------------------------------------------------

step "Configuration (.env)"
poetry run python scripts/configure.py

# --- local database --------------------------------------------------------------

step "Local database"
# Homebrew's PostgreSQL makes your Mac account the superuser and trusts
# connections from this machine, so there is no password to give.
poetry run python scripts/setup_local_db.py --user "$(whoami)" --no-password --yes --no-test

# --- ngrok -----------------------------------------------------------------------

ngrok_has_token() {
  local file
  for file in "$HOME/Library/Application Support/ngrok/ngrok.yml" "$HOME/.config/ngrok/ngrok.yml"; do
    if grep -qsE '^[[:space:]]*authtoken:[[:space:]]*[^[:space:]]+' "$file"; then
      return 0
    fi
  done
  return 1
}

step "ngrok"
if [ -n "$NGROK_MISSING" ]; then
  note "not installed, so its authtoken is asked for when it is"
elif ngrok_has_token; then
  ok "authtoken configured"
else
  echo "    ngrok gives WhatsApp a public address for this Mac. It needs your authtoken,"
  echo "    from https://dashboard.ngrok.com/get-started/your-authtoken"
  printf '    Paste it (hidden) and press Enter, or just Enter to skip: '
  NGROK_TOKEN=""
  read -r -s NGROK_TOKEN || true
  echo
  if [ -n "$NGROK_TOKEN" ]; then
    ngrok config add-authtoken "$NGROK_TOKEN" >/dev/null && ok "authtoken saved"
  else
    note "skipped. Later: ngrok config add-authtoken <your token>"
  fi
fi

# --- tests -----------------------------------------------------------------------

step "Tests"
if result="$(poetry run pytest -q 2>&1)"; then
  ok "unit tests: $(printf '%s\n' "$result" | tail -1)"
else
  printf '%s\n' "$result" > "$ROOT/logs/setup-unit-tests.log"
  note "unit tests failed; details in logs/setup-unit-tests.log"
fi

if poetry run python scripts/integration_test.py > "$ROOT/logs/setup-integration.log" 2>&1; then
  ok "database checks: $(grep -E 'RESULT' "$ROOT/logs/setup-integration.log" | tail -1 | sed -E 's/^RESULT: //')"
else
  note "database checks failed; details in logs/setup-integration.log"
fi

# --- done ------------------------------------------------------------------------

if [ -n "$NGROK_MISSING" ]; then
  step "Almost done"
  cat <<'EOF'
    Everything is set up except ngrok, which could not be downloaded. Your
    network may block ngrok's download site. Connect this Mac to your phone's
    hotspot, run the setup again (it only does what is missing), then switch
    back to your usual WiFi:

        ./scripts/setup_mac.sh
EOF
  exit 1
fi

step "Done"
cat <<'EOF'
    Start the bot:

        ./scripts/start.sh

    Then open http://localhost:8000/dashboard
EOF
