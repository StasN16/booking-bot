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

    updating = ENV.exists()
    if updating:
        # Never clobber an existing .env: it holds API keys and tokens that
        # are not recoverable from .env.example.
        print(f"{ENV.name} exists - updating DATABASE_URL, keeping everything else.")

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

    if updating:
        text = merge_into_existing(ENV.read_text(), url)
        print(f"\nUpdated {ENV.name}, other values untouched.")
    else:
        text = re.sub(r"^DATABASE_URL=.*$", f"DATABASE_URL={url}",
                      EXAMPLE.read_text(), count=1, flags=re.M)
        print(f"\nWrote {ENV.name} (gitignored)")

    ENV.write_text(text)

    if input("\nRun the integration test now? [Y/n] ").strip().lower() in ("", "y"):
        print()
        return subprocess.call([sys.executable, str(ROOT / "scripts" / "integration_test.py")])

    print("Later:  python3 scripts/integration_test.py")
    return 0


def merge_into_existing(text: str, url: str) -> str:
    """
    Replace DATABASE_URL in an existing .env, preserving every other line.

    Settings added later (BUSINESS_ID, TIMEZONE) are appended when absent so
    an older .env keeps working without being rewritten.
    """
    if re.search(r"^\s*DATABASE_URL=", text, flags=re.M):
        text = re.sub(r"^\s*DATABASE_URL=.*$", f"DATABASE_URL={url}",
                      text, count=1, flags=re.M)
    else:
        text = text.rstrip("\n") + f"\nDATABASE_URL={url}\n"

    defaults = {
        "BUSINESS_ID": "550e8400-e29b-41d4-a716-446655440000",
        "TIMEZONE": "Asia/Jerusalem",
    }
    missing = [k for k in defaults if not re.search(rf"^\s*{k}=", text, flags=re.M)]
    if missing:
        text = text.rstrip("\n") + "\n"
        for key in missing:
            text += f"{key}={defaults[key]}\n"
            print(f"  added missing {key}")

    return text


def quote_password(password: str) -> str:
    """Percent-encode characters that would otherwise break the URL."""
    from urllib.parse import quote
    return quote(password, safe="")


if __name__ == "__main__":
    sys.exit(main())
