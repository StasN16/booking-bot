"""
Apply migrations to Supabase without disturbing local development.

    poetry run python scripts/migrate_supabase.py

Finds the Supabase URL already recorded in .env, checks it is reachable,
and runs alembic against it by way of an environment variable. .env is
never modified, so local development stays pointed at the local database
and there is no half-switched state to undo if something fails.

Home and office networks often block outbound 5432, in which case this
cannot connect. A phone hotspot is the usual way through.
"""
import os
import pathlib
import re
import socket
import subprocess
import sys
from urllib.parse import urlsplit

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"


def main():
    print("Supabase migration")
    print("=" * 55)

    url = find_supabase_url()
    if not url:
        print("No Supabase URL found in .env.")
        url = input("Paste it (postgresql+asyncpg://...): ").strip()
    if not url:
        sys.exit("Nothing to do.")

    print(f"Target: {mask(url)}")

    host, port = host_and_port(url)
    if host and not reachable(host, port):
        sys.exit(
            f"\nCannot reach {host}:{port}.\n\n"
            "Most likely this network blocks outbound database connections.\n"
            "Try again on a phone hotspot, or check the project is not paused."
        )
    print("Reachable.\n")

    # Passed through the environment, which takes precedence over .env, so
    # the file itself is left alone.
    env = dict(os.environ, DATABASE_URL=url)

    print("Applying migrations ...")
    if subprocess.call([sys.executable, "-m", "alembic", "upgrade", "head"],
                       cwd=ROOT, env=env) != 0:
        sys.exit("\nMigration failed. Nothing in .env was changed.")

    print("\n" + "=" * 55)
    print("Supabase is up to date. .env still points where it did.")
    print("=" * 55)

    if input("\nRun the integration test against Supabase? [y/N] ").strip().lower() == "y":
        print()
        return subprocess.call(
            [sys.executable, str(ROOT / "scripts" / "integration_test.py")],
            cwd=ROOT, env=env,
        )
    return 0


def find_supabase_url() -> str | None:
    """Pick the Supabase URL out of .env, active or kept as a comment."""
    if not ENV.exists():
        return None

    candidates = []
    for line in ENV.read_text().splitlines():
        match = re.match(r"^\s*(?:#\s*)?DATABASE_URL(?:\s*\(previous\))?\s*[:=]\s*(\S+)", line)
        if match:
            candidates.append(match.group(1))

    for url in candidates:
        if "supabase" in url:
            return url
    return None


def host_and_port(url: str):
    try:
        parts = urlsplit(url)
        return parts.hostname, parts.port or 5432
    except Exception:
        return None, None


def reachable(host: str, port: int, timeout: int = 10) -> bool:
    print(f"Checking {host}:{port} ...")
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except Exception:
        return False


def mask(url: str) -> str:
    return re.sub(r"://([^:]+):[^@]*@", r"://\1:***@", url)


if __name__ == "__main__":
    sys.exit(main())
