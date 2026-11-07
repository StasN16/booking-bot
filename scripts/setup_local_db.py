"""
Point the project at a PostgreSQL server running on this machine.

    poetry run python scripts/setup_local_db.py

Creates the database, applies the migrations, seeds the demo clinic and
switches DATABASE_URL in .env. Any previous URL is kept as a comment, so
switching back to Supabase is a copy and paste.

Defaults follow the platform's usual install. Homebrew on a Mac creates a
superuser named after your account and trusts local connections, so there
is no password; the Windows installer creates "postgres" with the password
chosen during install. Everything can be overridden:

    --user NAME  --password PW | --no-password  --host H  --port P
    --yes        accept the defaults without asking
    --no-test    skip the integration test at the end

A local database also works on networks that block port 5432 outbound,
which is what makes it worth having.
"""
import argparse
import getpass
import os
import pathlib
import platform
import re
import subprocess
import sys
from urllib.parse import quote

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
DB_NAME = "bookingbot"
PREVIOUS_MARKER = "# DATABASE_URL (previous):"

# Values copied from .env.example rather than written by a person.
PLACEHOLDER_MARKERS = ("PROJECT_REF", "USER:PASS", ":PASSWORD@")


def default_user() -> str:
    """The superuser a normal install of PostgreSQL creates on this OS."""
    if platform.system() == "Windows":
        return "postgres"
    return getpass.getuser()


def main(argv=None):
    args = parse_args(argv)

    print("Local PostgreSQL setup")
    print("=" * 55)

    try:
        import psycopg2
    except ImportError:
        sys.exit(
            "psycopg2 is not installed.\n"
            "Run this through poetry:\n"
            "    poetry run python scripts/setup_local_db.py"
        )

    host = args.host or ask("Host", "localhost", args.yes)
    port = args.port or ask("Port", "5432", args.yes)
    user = args.user or ask("Superuser", default_user(), args.yes)

    if args.no_password:
        password = ""
    elif args.password is not None:
        password = args.password
    elif args.yes:
        password = ""
    else:
        password = getpass.getpass(
            f"Password for '{user}' (press Enter if there is none): "
        ).strip()

    print(f"\nConnecting to {host}:{port} as {user} ...")
    connect_args = dict(host=host, port=port, user=user,
                        dbname="postgres", connect_timeout=10)
    if password:
        connect_args["password"] = password
    try:
        conn = psycopg2.connect(**connect_args)
    except Exception as e:
        sys.exit(
            f"Could not connect: {e}\n"
            "Check that PostgreSQL is running "
            "(on a Mac: brew services start postgresql@16)\n"
            "and that the user and password are right."
        )

    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,))
        if cur.fetchone():
            print(f"Database '{DB_NAME}' already exists.")
        else:
            cur.execute(f'CREATE DATABASE "{DB_NAME}"')
            print(f"Created database '{DB_NAME}'.")
    conn.close()

    url = build_url(user, password, host, port)
    write_env(url)

    # Children must use this database even if the shell exported another
    # DATABASE_URL, which would otherwise outrank the .env just written.
    child_env = dict(os.environ, DATABASE_URL=url)

    if not run("migrations", [sys.executable, "-m", "alembic", "upgrade", "head"],
               child_env):
        return 1
    if not run("seed data", [sys.executable, str(ROOT / "scripts" / "seed.py")],
               child_env):
        return 1

    print("\n" + "=" * 55)
    print("Local database ready. .env now points at it.")
    print("=" * 55)

    if args.no_test:
        return 0
    if args.yes or input("\nRun the integration test against it? [Y/n] ").strip().lower() in ("", "y"):
        print()
        return subprocess.call(
            [sys.executable, str(ROOT / "scripts" / "integration_test.py")],
            cwd=ROOT, env=child_env,
        )
    return 0


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host")
    parser.add_argument("--port")
    parser.add_argument("--user")
    secret = parser.add_mutually_exclusive_group()
    secret.add_argument("--password")
    secret.add_argument("--no-password", action="store_true")
    parser.add_argument("--yes", action="store_true",
                        help="accept defaults without asking")
    parser.add_argument("--no-test", action="store_true",
                        help="skip the integration test")
    return parser.parse_args(argv)


def ask(label: str, default: str, accept_default: bool) -> str:
    if accept_default:
        return default
    return input(f"{label} [{default}]: ").strip() or default


def build_url(user: str, password: str, host: str, port: str) -> str:
    """An asyncpg URL, leaving the password out entirely when there is none."""
    credentials = quote(user, safe="")
    if password:
        credentials += ":" + quote(password, safe="")
    return f"postgresql+asyncpg://{credentials}@{host}:{port}/{DB_NAME}"


def is_placeholder(url: str) -> bool:
    return not url or any(marker in url for marker in PLACEHOLDER_MARKERS)


def write_env(url: str):
    """
    Make `url` the active DATABASE_URL, keeping earlier ones as comments.

    Every earlier URL is kept, not just the last. Re-running this used to
    replace the remembered Supabase URL with nothing, because the active URL
    was already the local one and the old comment was dropped regardless.
    """
    if not ENV.exists():
        ENV.write_text(f"DATABASE_URL={url}\n")
        print(f"Wrote {ENV.name}.")
        return

    lines = ENV.read_text().splitlines()

    previous = []
    for line in lines:
        if line.startswith(PREVIOUS_MARKER):
            previous.append(line[len(PREVIOUS_MARKER):].strip())

    current = next(
        (line.split("=", 1)[1].strip() for line in lines
         if re.match(r"^\s*DATABASE_URL=", line)),
        "",
    )
    if current and not is_placeholder(current):
        previous.insert(0, current)

    # Distinct, in order, never the URL being made active, never a placeholder.
    kept = []
    for old in previous:
        if old and old != url and not is_placeholder(old) and old not in kept:
            kept.append(old)

    block = [f"{PREVIOUS_MARKER} {old}" for old in kept] + [f"DATABASE_URL={url}"]

    out, placed = [], False
    for line in lines:
        is_db_line = line.startswith(PREVIOUS_MARKER) or re.match(
            r"^\s*DATABASE_URL=", line)
        if is_db_line:
            if not placed:
                out.extend(block)
                placed = True
            continue
        out.append(line)
    if not placed:
        out.extend(block)

    ENV.write_text("\n".join(out) + "\n")
    note = f" ({len(kept)} earlier URL(s) kept as comments)" if kept else ""
    print(f"Updated {ENV.name}{note}.")


def run(label: str, command: list, env: dict) -> bool:
    print(f"\nApplying {label} ...")
    if subprocess.call(command, cwd=ROOT, env=env) != 0:
        print(f"\nFailed while applying {label}.")
        return False
    return True


if __name__ == "__main__":
    sys.exit(main())
