"""
The password every clinic user starts with.

    poetry run python scripts/start_password.py YOUR-START-PASSWORD
    poetry run python scripts/start_password.py YOUR-START-PASSWORD --everyone

Saves it in .env as CLINIC_START_PASSWORD. From then on, every user added
on the Clinics page, and every New password given there, gets it. It is
only a start: signing in with it, a user must choose their own password
before anything else opens.

--everyone also gives it, now, to every clinic user there already is. Each
of them chooses their own at their next sign-in. The owner's password, in
.env as ADMIN_PASSWORD, is not touched.

Restart the server afterwards (./scripts/start.sh) so it reads the change.
"""
import argparse
import asyncio
import pathlib
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(SCRIPTS.parent), str(SCRIPTS)]

import configure  # noqa: E402  (reads and writes .env)
from app.core.passwords import MIN_LENGTH  # noqa: E402

KEY = "CLINIC_START_PASSWORD"


def problem_with(password: str) -> str | None:
    if len(password) < MIN_LENGTH:
        return f"It needs at least {MIN_LENGTH} characters."
    if password != password.strip():
        return "It starts or ends with a space, which is easy to get wrong when typing it."
    if configure.UNSTORABLE in password:
        return f"It contains {configure.UNSTORABLE!r}, which .env cannot store."
    return None


async def give_everyone(password: str) -> list[str]:
    # Every table, so the database mappings can be set up as in the server.
    from app.core.models import appointment, business, customer, google_token, therapist, treatment, user  # noqa: F401
    from app.core.db import engine
    from app.services import accounts
    try:
        return await accounts.give_everyone(password)
    finally:
        await engine.dispose()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Set the password clinic users start with")
    parser.add_argument("password", help=f"at least {MIN_LENGTH} characters")
    parser.add_argument("--everyone", action="store_true",
                        help="also give it now to every clinic user there already is")
    args = parser.parse_args(argv)

    problem = problem_with(args.password)
    if problem:
        print(f"Not saved. {problem}")
        return 1

    # The users first: if the database cannot be reached, nothing changes,
    # and running this again is all it takes.
    emails = None
    if args.everyone:
        try:
            emails = asyncio.run(give_everyone(args.password))
        except Exception as e:
            first_line = (str(e).strip().splitlines() or [type(e).__name__])[0]
            print(f"Nothing was changed: the database could not be updated ({first_line}).")
            print("Is PostgreSQL running? Start it with: brew services start postgresql@16")
            return 1

    configure.save(configure.put(configure.load(), KEY, args.password))
    print(f"Saved in .env as {KEY}. New clinic users start with it,")
    print("and choose their own password the first time they sign in.")

    if emails:
        print(f"\n{len(emails)} clinic user(s) now have it too, and choose their own at their next sign-in:")
        for email in emails:
            print(f"  {email}")
    elif emails is not None:
        print("\nThere are no clinic users yet.")

    print("\nRestart the bot so it uses the change: Ctrl+C, then ./scripts/start.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
