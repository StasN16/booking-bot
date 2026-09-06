"""
Create a local .env and check the database connection.

    python3 scripts/setup_env.py

Prompts for the database password without echoing it, writes .env from
.env.example, and offers to run the integration test. .env is gitignored,
so the password stays on this machine.
"""
import getpass
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"

DEFAULT_HOST = "aws-1-eu-central-1.pooler.supabase.com:5432"
DEFAULT_DB = "postgres"


def main():
    if not EXAMPLE.exists():
        sys.exit(f"missing {EXAMPLE}")

    if ENV.exists():
        answer = input(f"{ENV.name} already exists. Overwrite? [y/N] ").strip().lower()
        if answer != "y":
            print("Left the existing .env alone.")
            return 0

    print("Supabase connection")
    print("-" * 50)
    project_ref = input("Project ref [ipenykslvztwrdijiezj]: ").strip() or "ipenykslvztwrdijiezj"
    host = input(f"Host [{DEFAULT_HOST}]: ").strip() or DEFAULT_HOST

    # Pooler connections authenticate as postgres.<project_ref>, not postgres.
    user = f"postgres.{project_ref}"
    print(f"Username: {user}")

    password = getpass.getpass("Database password (not shown): ").strip()
    if not password:
        sys.exit("No password entered, nothing written.")

    quoted = quote_password(password)
    url = f"postgresql+asyncpg://{user}:{quoted}@{host}/{DEFAULT_DB}"

    text = EXAMPLE.read_text()
    text = re.sub(r"^DATABASE_URL=.*$", f"DATABASE_URL={url}", text, count=1, flags=re.M)
    ENV.write_text(text)
    print(f"\nWrote {ENV} (gitignored)")

    if input("\nRun the integration test now? [Y/n] ").strip().lower() in ("", "y"):
        print()
        return subprocess.call([sys.executable, str(ROOT / "scripts" / "integration_test.py")])

    print("Later:  python3 scripts/integration_test.py")
    return 0


def quote_password(password: str) -> str:
    """Percent-encode characters that would otherwise break the URL."""
    from urllib.parse import quote
    return quote(password, safe="")


if __name__ == "__main__":
    sys.exit(main())
