"""
Fill in .env for this machine, asking only for what is missing.

    poetry run python scripts/configure.py
    poetry run python scripts/configure.py --check     only test what is set

If you have a .env from another machine, copy it into the project folder
first: every value already set is kept. Anything missing is asked for, the
dashboard's signing secret is generated, and the OpenAI and WhatsApp
credentials are tried against their real APIs, so a bad paste shows up now
rather than later as a bot that quietly never replies.
"""
import argparse
import getpass
import pathlib
import re
import secrets
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"

sys.path.insert(0, str(ROOT))

# (key, label, hidden input, where to find it)
ASKED = [
    ("OPENAI_API_KEY", "OpenAI API key", True,
     "platform.openai.com/api-keys, or the OPENAI_API_KEY line of your old .env"),
    ("WHATSAPP_TOKEN", "WhatsApp access token", True,
     "the permanent token of system user 'bookingbot', or WHATSAPP_TOKEN in your "
     "old .env. Lost it? business.facebook.com/settings/system-users -> bookingbot "
     "-> Generate token, expiry Never, permissions whatsapp_business_messaging "
     "and whatsapp_business_management"),
    ("WHATSAPP_PHONE_ID", "WhatsApp phone number ID", False,
     "Meta app -> WhatsApp -> API Setup, the ID under the From number"),
    ("WHATSAPP_VERIFY_TOKEN", "Webhook verify token", False,
     "the Verify token typed into Meta's webhook settings"),
    ("ADMIN_PASSWORD", "Dashboard password", True,
     "you choose it. Press Enter and one is generated for you"),
]

GENERATED = {
    # Signs dashboard logins. Nobody types it, so it is simply made.
    "JWT_SECRET": lambda: secrets.token_urlsafe(32),
}

# Characters that need quoting for python-dotenv to read the value back
# unchanged: whitespace, a comment marker, or quotes.
NEEDS_QUOTES = re.compile(r"""[\s#'"\\]""")

# python-dotenv expands ${NAME} inside any value, quoted or not, and offers
# no escape for it. A value containing "${" would be silently rewritten on
# the way back in, so it is refused rather than stored.
UNSTORABLE = "${"


# --- reading and writing .env ------------------------------------------------

def parse_value(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        inner = raw[1:-1]
        if raw[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner
    return raw


def format_value(value: str) -> str:
    if not NEEDS_QUOTES.search(value):
        return value
    if "'" not in value:
        return f"'{value}'"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def get(lines: list[str], key: str) -> str:
    """The value of `key`, from the last uncommented line that sets it."""
    value = ""
    for line in lines:
        match = re.match(rf"^\s*{re.escape(key)}\s*=(.*)$", line)
        if match:
            value = parse_value(match.group(1))
    return value


def put(lines: list[str], key: str, value: str) -> list[str]:
    """Set `key`, replacing an existing line or appending a new one."""
    entry = f"{key}={format_value(value)}"
    out, done = [], False
    for line in lines:
        if re.match(rf"^\s*{re.escape(key)}\s*=", line):
            if not done:
                out.append(entry)
                done = True
            continue  # drop duplicates so the file has one answer
        out.append(line)
    if not done:
        out.append(entry)
    return out


def load() -> list[str]:
    if not ENV.exists():
        if EXAMPLE.exists():
            shutil.copy(EXAMPLE, ENV)
            print(f"Created {ENV.name} from {EXAMPLE.name}.")
        else:
            ENV.write_text("")
    return ENV.read_text().splitlines()


def save(lines: list[str]):
    ENV.write_text("\n".join(lines).rstrip("\n") + "\n")
    try:
        ENV.chmod(0o600)  # secrets: readable by this account only
    except OSError:
        pass


# --- checking credentials against the real services ---------------------------

def check_openai(key: str) -> tuple[bool | None, str]:
    import httpx
    try:
        response = httpx.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=15,
        )
    except Exception as e:
        return None, f"could not reach OpenAI ({type(e).__name__})"
    if response.status_code == 200:
        return True, "OpenAI key works"
    if response.status_code == 401:
        return False, "OpenAI rejected the key (wrong, revoked, or pasted incompletely)"
    return False, f"OpenAI answered {response.status_code}"


def check_whatsapp(token: str, phone_id: str) -> tuple[bool | None, str]:
    import httpx
    try:
        from app.services.whatsapp import WHATSAPP_API_URL
    except Exception:
        WHATSAPP_API_URL = "https://graph.facebook.com/v18.0"
    try:
        response = httpx.get(
            f"{WHATSAPP_API_URL}/{phone_id}",
            params={"fields": "display_phone_number,verified_name"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
    except Exception as e:
        return None, f"could not reach Meta ({type(e).__name__})"

    if response.status_code == 200:
        data = response.json()
        number = data.get("display_phone_number", "?")
        name = data.get("verified_name", "")
        return True, f"WhatsApp token works for {number} {name}".rstrip()

    try:
        error = response.json().get("error", {})
    except Exception:
        error = {}
    code = error.get("code")
    if code == 190:
        return False, "Meta rejected the WhatsApp token (expired, revoked or incomplete)"
    if code == 100:
        return False, "Meta does not know that phone number ID for this token"
    return False, f"Meta answered {response.status_code}: {error.get('message', '')[:120]}"


def run_checks(lines: list[str]) -> bool:
    print("\nChecking credentials against the real services ...")
    all_good = True

    key = get(lines, "OPENAI_API_KEY")
    if key:
        ok, message = check_openai(key)
        print(f"  {mark(ok)} {message}")
        all_good &= ok is not False
    else:
        print(f"  {mark(False)} OPENAI_API_KEY is empty: the bot cannot think")
        all_good = False

    token, phone_id = get(lines, "WHATSAPP_TOKEN"), get(lines, "WHATSAPP_PHONE_ID")
    if token and phone_id:
        ok, message = check_whatsapp(token, phone_id)
        print(f"  {mark(ok)} {message}")
        all_good &= ok is not False
    else:
        print(f"  {mark(False)} WHATSAPP_TOKEN or WHATSAPP_PHONE_ID is empty: "
              f"the bot cannot reply")
        all_good = False

    return all_good


def mark(ok: bool | None) -> str:
    return {True: "ok  ", False: "FAIL", None: "??  "}[ok]


# --- the interactive part ------------------------------------------------------

def ask(label: str, hidden: bool, hint: str) -> str:
    print(f"\n{label}")
    print(f"  where: {hint}")
    while True:
        if hidden:
            value = getpass.getpass(
                "  value (typing is hidden, paste and press Enter): ").strip()
        else:
            value = input("  value: ").strip()
        if UNSTORABLE not in value:
            return value
        print(f"  That contains {UNSTORABLE!r}, which .env cannot store "
              f"reliably. Please use a different value.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Fill in .env for this machine")
    parser.add_argument("--check", action="store_true",
                        help="only test the credentials already in .env")
    args = parser.parse_args(argv)

    lines = load()

    if args.check:
        return 0 if run_checks(lines) else 1

    print("Configuration")
    print("=" * 55)

    for key, make in GENERATED.items():
        if not get(lines, key):
            lines = put(lines, key, make())
            print(f"Generated {key}.")

    generated_password = None
    skipped = []
    for key, label, hidden, hint in ASKED:
        if get(lines, key):
            print(f"{key}: already set")
            continue
        value = ask(label, hidden, hint)
        if not value and key == "ADMIN_PASSWORD":
            value = generated_password = secrets.token_urlsafe(9)
        if value:
            lines = put(lines, key, value)
        else:
            skipped.append(key)
        save(lines)  # keep what was entered even if the run is interrupted

    save(lines)
    print(f"\nSaved {ENV.name} (readable by your account only).")

    if generated_password:
        print("\n" + "-" * 55)
        print(f"Your dashboard password is:  {generated_password}")
        print("It is saved in .env as ADMIN_PASSWORD.")
        print("-" * 55)

    if skipped:
        print(f"\nStill empty: {', '.join(skipped)}. "
              f"Run this again when you have them.")

    run_checks(lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
