"""
Point the project at a PostgreSQL server running on this machine.

Install PostgreSQL first (https://www.postgresql.org/download/), then:

    poetry run python scripts/setup_local_db.py

Creates the database, applies the migrations, seeds the demo clinic and
switches DATABASE_URL in .env. The previous URL is kept as a comment, so
switching back to Supabase is a copy and paste.

A local database also works on networks that block port 5432 outbound,
which is what makes it worth having.
"""
import getpass
import pathlib
import re
import subprocess
import sys
from urllib.parse import quote

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
DB_NAME = "bookingbot"
PREVIOUS_MARKER = "# DATABASE_URL (previous):"


def main():
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

    host = input("Host [localhost]: ").strip() or "localhost"
    port = input("Port [5432]: ").strip() or "5432"
    user = input("Superuser [postgres]: ").strip() or "postgres"
    password = getpass.getpass(f"Password for '{user}' (set during install): ").strip()

    if not password:
        sys.exit("No password entered, stopping.")

    print(f"\nConnecting to {host}:{port} ...")
    try:
        conn = psycopg2.connect(
            host=host, port=port, user=user, password=password,
            dbname="postgres", connect_timeout=10,
        )
    except Exception as e:
        sys.exit(
            f"Could not connect: {e}\n\n"
            "Check that PostgreSQL is installed and running, and that the\n"
            "password matches the one set during installation."
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

    url = f"postgresql+asyncpg://{user}:{quote(password, safe='')}@{host}:{port}/{DB_NAME}"
    write_env(url)

    if not run("migrations", [sys.executable, "-m", "alembic", "upgrade", "head"]):
        return 1
    if not run("seed data", [sys.executable, str(ROOT / "scripts" / "seed.py")]):
        return 1

    print("\n" + "=" * 55)
    print("Local database ready. .env now points at it.")
    print("=" * 55)

    if input("\nRun the integration test against it? [Y/n] ").strip().lower() in ("", "y"):
        print()
        return subprocess.call(
            [sys.executable, str(ROOT / "scripts" / "integration_test.py")], cwd=ROOT
        )
    return 0


def write_env(url: str):
    """Switch DATABASE_URL, keeping the old value as a recoverable comment."""
    if not ENV.exists():
        ENV.write_text(f"DATABASE_URL={url}\n")
        print(f"Wrote {ENV.name}.")
        return

    lines = ENV.read_text().splitlines()
    # Drop the marker from a previous run so they don't pile up.
    lines = [l for l in lines if not l.startswith(PREVIOUS_MARKER)]

    out, replaced = [], False
    for line in lines:
        if re.match(r"^\s*DATABASE_URL=", line) and not replaced:
            old = line.split("=", 1)[1].strip()
            if old and old != url:
                out.append(f"{PREVIOUS_MARKER} {old}")
            out.append(f"DATABASE_URL={url}")
            replaced = True
        else:
            out.append(line)

    if not replaced:
        out.append(f"DATABASE_URL={url}")

    ENV.write_text("\n".join(out) + "\n")
    print(f"Updated {ENV.name} (previous URL kept as a comment).")


def run(label: str, command: list) -> bool:
    print(f"\nApplying {label} ...")
    if subprocess.call(command, cwd=ROOT) != 0:
        print(f"\nFailed while applying {label}.")
        return False
    return True


if __name__ == "__main__":
    sys.exit(main())
