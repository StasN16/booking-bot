#!/usr/bin/env bash
#
# Start the bot on this Mac: the server, plus an ngrok tunnel so WhatsApp can
# reach it. Ctrl+C stops both.
#
#   ./scripts/start.sh
#
# Prints the address WhatsApp has to call and warns when it differs from the
# last start. A changed address is the one failure that is otherwise silent:
# Meta keeps posting to the old one and messages simply stop arriving.
#
# Set NGROK_DOMAIN in .env to pin a reserved ngrok domain.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${PORT:-8000}"
LOGS="$ROOT/logs"
URL_FILE="$LOGS/public_url.txt"
mkdir -p "$LOGS"

bold=$'\033[1m'; green=$'\033[32m'; yellow=$'\033[33m'; red=$'\033[31m'; reset=$'\033[0m'
note() { printf '  %s!%s %s\n' "$yellow" "$reset" "$*"; }
die()  { printf '\n%s✗ %s%s\n' "$red" "$*" "$reset" >&2; exit 1; }

# Homebrew's tools, for a shell that has not read ~/.zprofile.
if ! command -v brew >/dev/null 2>&1; then
  for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [ -x "$candidate" ]; then
      eval "$("$candidate" shellenv)"
      break
    fi
  done
fi

[ -f .env ] || die "There is no .env yet. Run ./scripts/setup_mac.sh first."
command -v poetry >/dev/null 2>&1 || die "Poetry is missing. Run ./scripts/setup_mac.sh first."

env_value() {
  # The last uncommented KEY=value in .env, without quotes or a Windows
  # line ending (a .env copied from Windows has those).
  { grep -E "^[[:space:]]*$1[[:space:]]*=" .env || true; } | tail -1 | cut -d= -f2- \
    | tr -d '\r' | sed -E "s/^[[:space:]]*['\"]?//; s/['\"]?[[:space:]]*$//"
}

# --- database ------------------------------------------------------------------

if command -v brew >/dev/null 2>&1; then
  PG_READY="$(brew --prefix postgresql@16)/bin/pg_isready"
  if [ -x "$PG_READY" ] && ! "$PG_READY" -q -h localhost; then
    printf 'Starting PostgreSQL ...\n'
    brew services start postgresql@16 >/dev/null
    waited=0
    until "$PG_READY" -q -h localhost; do
      waited=$((waited + 1))
      [ "$waited" -le 20 ] || die "PostgreSQL did not start. Try: brew services restart postgresql@16"
      sleep 1
    done
  fi
fi

# Bring the database up to date with the code, so a newly pulled version
# never starts against tables it expects and the database does not have yet.
if ! poetry run alembic upgrade head > "$LOGS/migrate.log" 2>&1; then
  die "Could not update the database. Details are in logs/migrate.log"
fi

# --- port ----------------------------------------------------------------------

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  die "Port $PORT is already in use. Is the bot already running in another window?"
fi

# --- ngrok ---------------------------------------------------------------------

NGROK_PID=""
cleanup() {
  trap - EXIT INT TERM
  if [ -n "$NGROK_PID" ]; then
    kill "$NGROK_PID" 2>/dev/null || true
  fi
  exit 0
}
trap cleanup EXIT INT TERM

tunnel_url() {
  # The https address of the ngrok agent running on this machine, if any,
  # read from the agent's local API.
  { curl -fs --max-time 2 http://127.0.0.1:4040/api/tunnels 2>/dev/null || true; } \
    | { grep -oE '"public_url"[[:space:]]*:[[:space:]]*"https://[^"]+"' || true; } \
    | head -1 | cut -d'"' -f4
}

public_url="$(tunnel_url)"
if [ -n "$public_url" ]; then
  note "Using the ngrok tunnel that is already running."
elif command -v ngrok >/dev/null 2>&1; then
  domain="$(env_value NGROK_DOMAIN)"
  domain="${domain#https://}"
  if [ -n "$domain" ]; then
    ngrok http "$PORT" --url="https://$domain" --log=stdout > "$LOGS/ngrok.log" 2>&1 &
  else
    ngrok http "$PORT" --log=stdout > "$LOGS/ngrok.log" 2>&1 &
  fi
  NGROK_PID=$!

  tries=0
  while [ -z "$public_url" ] && [ "$tries" -lt 40 ]; do
    kill -0 "$NGROK_PID" 2>/dev/null || break   # it gave up
    sleep 0.25
    public_url="$(tunnel_url)"
    tries=$((tries + 1))
  done

  if [ -z "$public_url" ]; then
    note "ngrok did not start, so WhatsApp cannot reach this Mac. The dashboard still works."
    { grep -iE 'err|fail|authtoken' "$LOGS/ngrok.log" || true; } | tail -3 | sed 's/^/      /'
    note "Full log: logs/ngrok.log"
  fi
else
  note "ngrok is not installed, so WhatsApp cannot reach this Mac. Install: brew install ngrok"
fi

# --- what to use ----------------------------------------------------------------

printf '\n%sBooking bot%s\n' "$bold" "$reset"
printf '  Dashboard     http://localhost:%s/dashboard\n' "$PORT"
printf '  API docs      http://localhost:%s/docs\n' "$PORT"

if [ -n "$public_url" ]; then
  webhook="$public_url/api/v1/webhook"
  printf '  Webhook URL   %s%s%s\n' "$green" "$webhook" "$reset"

  previous="$(cat "$URL_FILE" 2>/dev/null || true)"
  if [ -z "$previous" ]; then
    printf '\n'
    note "First start on this machine: check that Meta's callback URL is exactly"
    note "the Webhook URL above (WhatsApp -> Configuration -> Callback URL)."
  elif [ "$previous" != "$webhook" ]; then
    printf '\n  %s%sThe webhook URL changed since the last start.%s\n' "$bold" "$yellow" "$reset"
    printf '    was  %s\n' "$previous"
    printf '    now  %s\n' "$webhook"
    printf '  WhatsApp messages will not arrive until you update it in Meta:\n'
    printf '  WhatsApp -> Configuration -> Callback URL -> Edit\n'
  fi
  printf '%s\n' "$webhook" > "$URL_FILE"
fi

printf '\n  Ctrl+C stops everything.\n\n'

poetry run uvicorn app.main:app --reload --port "$PORT"
