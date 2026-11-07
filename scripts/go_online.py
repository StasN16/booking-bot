"""
Put the bot online on Render, with every value checked on the way.

    ./scripts/go_online.sh                    start to finish
    ./scripts/go_online.sh https://ADDRESS    only the end, for a server already up

Start to finish, in one run:

1. The values Render's Blueprint page asks for, each checked. Most are in
   .env and are tried against their services. The new ones are typed here,
   hidden: a new owner password, Meta's App secret, and the Supabase
   address unless .env still has it.
2. Render: each value is put on the clipboard in turn, to paste into its
   box. Nothing typed is saved, and the clipboard is emptied afterwards.
3. The server, once it answers: checked for the same settings, WhatsApp's
   webhook pointed at it, and a clinic made to answer the WhatsApp number.
"""
import argparse
import asyncio
import getpass
import hashlib
import hmac
import json
import pathlib
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

import httpx

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(SCRIPTS.parent), str(SCRIPTS)]

import configure  # noqa: E402  (.env, and the OpenAI and WhatsApp checks)
import migrate_supabase  # noqa: E402  (finds a Supabase address kept in .env)

GRAPH = "https://graph.facebook.com/v18.0"  # as app/services/whatsapp.py

# What Render's Blueprint page asks for: the `sync: false` values in
# render.yaml, in its order.
ASKED = [
    "DATABASE_URL", "ADMIN_PASSWORD", "OPENAI_API_KEY", "WHATSAPP_TOKEN",
    "WHATSAPP_VERIFY_TOKEN", "WHATSAPP_APP_SECRET", "WHATSAPP_PHONE_ID",
    "BUSINESS_ID", "CLINIC_START_PASSWORD",
]
MIN_OWNER_PASSWORD = 10
# A first deploy installs everything, so it takes a few minutes.
WAIT_MINUTES = 15
MARK = {True: "✓", False: "✗", None: "!"}
# What Meta posts when nothing happened: a harmless way to try the webhook.
PROBE = json.dumps({"object": "whatsapp_business_account", "entry": []}).encode()


# --- checks that need no network ------------------------------------------------

def raw_password(url: str) -> str:
    """The password as written in a database address, before any parsing."""
    rest = url.split("://", 1)[-1]
    return rest.rpartition("@")[0].partition(":")[2]


def database_url_problem(url: str) -> str | None:
    """What would stop this database address from working online, if anything."""
    url = url.strip()
    if not url:
        return "Nothing was pasted."
    if "[YOUR-PASSWORD]" in url:
        return "Put your database password where the address says [YOUR-PASSWORD]."
    if "PROJECT_REF" in url:
        return "That is the example from .env.example, not your address."
    if any(c in raw_password(url) for c in "@#/? "):
        return ("The database password has a character an address cannot hold (@ # / ? or a space). "
                "In Supabase: Project Settings → Database → Reset database password, letters and "
                "digits only; then copy the address again.")
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018  (raises for a port that is not a number)
    except ValueError:
        return "That address does not read right. Copy it again from Supabase."
    if parts.scheme not in ("postgres", "postgresql", "postgresql+asyncpg"):
        return "That is not a database address: those start with postgresql://"
    host = parts.hostname or ""
    if host in ("localhost", "127.0.0.1", "::1"):
        return "That is this Mac's own database, which Render cannot reach. Use Supabase's address."
    if host.startswith("db.") and host.endswith(".supabase.co"):
        return ("That is the Direct connection, which Render cannot reach. "
                "In Supabase: Connect → Session pooler.")
    if not parts.password:
        return "The address has no password in it."
    if parts.query:
        return "Leave out the part from the ? on; the Session pooler's address has none."
    return None


def session_pooler(url: str) -> str:
    """For Supabase's Transaction pooler, the Session pooler: the same address on port 5432."""
    url = url.strip()
    try:
        parts = urlsplit(url)
        if (parts.hostname or "").endswith(".pooler.supabase.com") and parts.port == 6543:
            return urlunsplit(parts._replace(netloc=parts.netloc.rsplit(":", 1)[0] + ":5432"))
    except ValueError:
        pass
    return url


def owner_password_problem(new: str, current: str) -> str | None:
    if len(new) < MIN_OWNER_PASSWORD:
        return f"At least {MIN_OWNER_PASSWORD} characters, please."
    if current and new == current:
        return "That is the one in .env, which was shared in a chat. Choose a new one."
    return None


def signature(secret: str, body: bytes) -> str:
    """The X-Hub-Signature-256 Meta would send with this body."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def server_address(address: str, allow_http: bool = False) -> str | None:
    """The server's base address (scheme and host), from whatever was pasted."""
    address = address.strip()
    if address and "://" not in address:
        address = "https://" + address
    try:
        parts = urlsplit(address)
    except ValueError:
        return None
    if parts.scheme not in (("https", "http") if allow_http else ("https",)) or not parts.hostname:
        return None
    if "." not in parts.hostname and parts.hostname != "localhost":
        return None  # a stray word, not an address
    return f"{parts.scheme}://{parts.netloc}"


def who_answers(clinics: list[dict], phone_id: str) -> dict | None:
    """
    The clinic the server gives messages to this number, by its rules
    (app/core/tenancy.py): the active clinic that has the number, or else
    the main clinic, BUSINESS_ID, whose number is the server's own.
    """
    if not phone_id:
        return None
    active = [clinic for clinic in clinics if clinic.get("is_active")]
    for clinic in active:
        if clinic.get("whatsapp_phone_id") == phone_id:
            return clinic
    for clinic in active:
        if clinic.get("is_home"):
            return clinic
    return None


# --- checks that ask a service ---------------------------------------------------

async def database_summary(url: str) -> tuple[bool | None, str]:
    """
    Connect and say what the database holds: (True, what) when it answers,
    (False, why) when it refuses, (None, why) when it cannot be reached.
    """
    import asyncpg
    dsn = "postgresql://" + url.strip().split("://", 1)[1]
    try:
        connection = await asyncpg.connect(dsn, timeout=20)
    except asyncpg.InvalidPasswordError:
        return False, "Supabase refused the password in the address."
    except asyncpg.PostgresError as e:
        return False, f"Supabase refused it ({e}). Is the project paused? Open it on supabase.com."
    except Exception as e:
        return None, f"Could not reach it from this network ({type(e).__name__})."
    try:
        if not await connection.fetchval("SELECT to_regclass('public.businesses') IS NOT NULL"):
            return True, "Connected. It is empty: the server sets it up when it starts."
        names = [row["name"] for row in await connection.fetch("SELECT name FROM businesses ORDER BY name")]
    finally:
        await connection.close()
    if not names:
        return True, "Connected. It has no clinics yet."
    return True, f"Connected. Its clinics: {', '.join(names)}."


def meta_app_id(token: str) -> str | None:
    """The Meta app the WhatsApp token belongs to."""
    try:
        response = httpx.get(f"{GRAPH}/app", headers={"Authorization": f"Bearer {token}"}, timeout=15)
        return response.json().get("id") if response.status_code == 200 else None
    except Exception:
        return None


def secret_is_the_apps(app_id: str, secret: str) -> bool | None:
    """Whether Meta accepts this as the app's secret. None: could not ask."""
    try:
        response = httpx.get(f"{GRAPH}/oauth/access_token", timeout=15, params={
            "client_id": app_id, "client_secret": secret, "grant_type": "client_credentials"})
        body = response.json()
    except Exception:
        return None
    if response.status_code >= 500:
        return None
    return response.status_code == 200 and "access_token" in body


def token_expiry(token: str, app_id: str, secret: str) -> int | None:
    """When the WhatsApp token stops working, in Unix time; 0 for never. None: could not ask."""
    try:
        response = httpx.get(f"{GRAPH}/debug_token", timeout=15, params={
            "input_token": token, "access_token": f"{app_id}|{secret}"})
        data = response.json().get("data") or {}
    except Exception:
        return None
    if response.status_code != 200 or not isinstance(data.get("expires_at"), int):
        return None
    return data["expires_at"]


def point_webhook(app_id: str, secret: str, callback: str, verify_token: str) -> tuple[bool, str]:
    """Tell Meta to send this app's WhatsApp messages to `callback`."""
    try:
        response = httpx.post(f"{GRAPH}/{app_id}/subscriptions", timeout=30, data={
            "object": "whatsapp_business_account",
            "callback_url": callback,
            "verify_token": verify_token,
            "fields": "messages",
            "access_token": f"{app_id}|{secret}",
        })
        body = response.json()
    except Exception as e:
        return False, f"Could not ask Meta ({type(e).__name__})."
    if response.status_code == 200 and body.get("success"):
        return True, "WhatsApp now sends messages to the new server."
    detail = (body.get("error") or {}).get("message", "") if isinstance(body, dict) else ""
    return False, f"Meta did not take it: {detail or response.status_code}"


def wait_until_up(base: str, minutes: float, every: float = 10.0,
                  sleep=time.sleep, clock=time.monotonic) -> bool:
    """Ask the server's /health until it answers, for up to `minutes`."""
    deadline = clock() + minutes * 60
    waiting = False
    while True:
        try:
            response = httpx.get(f"{base}/health", timeout=15)
            up = response.status_code == 200 and response.json().get("status") == "healthy"
        except Exception:
            up = False
        if up or clock() >= deadline:
            if waiting:
                print()
            return up
        if not waiting:
            print("    Waiting for it to start (the first time takes a few minutes) ", end="", flush=True)
            waiting = True
        print(".", end="", flush=True)
        sleep(every)


def holds_verify_token(base: str, verify: str) -> bool:
    """Whether the server answers Meta's check with this verify token."""
    challenge = str(secrets.randbelow(9 * 10**8) + 10**8)
    response = httpx.get(f"{base}/api/v1/webhook", timeout=20, params={
        "hub.mode": "subscribe", "hub.verify_token": verify, "hub.challenge": challenge})
    return response.status_code == 200 and response.text.strip() == challenge


def accepts(base: str, secret: str | None) -> bool:
    """Whether the server takes a webhook post signed with `secret`, or unsigned."""
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-Hub-Signature-256"] = signature(secret, PROBE)
    return httpx.post(f"{base}/api/v1/webhook", content=PROBE, headers=headers, timeout=20).status_code == 200


def sign_in(base: str, password: str) -> tuple[str | None, int]:
    """The owner's sign-in: a token, or None and the status code."""
    response = httpx.post(f"{base}/api/v1/auth/login", json={"password": password}, timeout=20)
    if response.status_code == 200:
        return response.json().get("access_token"), 200
    return None, response.status_code


def online_clinics(base: str, token: str) -> list[dict]:
    response = httpx.get(f"{base}/api/v1/platform/clinics", timeout=20,
                         headers={"Authorization": f"Bearer {token}"})
    response.raise_for_status()
    return response.json()


def give_number(base: str, token: str, clinic_id: str, phone_id: str) -> tuple[bool, str]:
    response = httpx.put(f"{base}/api/v1/platform/clinics/{clinic_id}", timeout=20,
                         json={"whatsapp_phone_id": phone_id},
                         headers={"Authorization": f"Bearer {token}"})
    if response.status_code == 200:
        return True, ""
    try:
        detail = response.json().get("detail", "")
    except ValueError:
        detail = ""
    return False, str(detail or f"the server answered {response.status_code}")


# --- the terminal ------------------------------------------------------------------

def copy(value: str) -> None:
    subprocess.run(["pbcopy"], input=value.encode(), check=True)


def hidden(label: str) -> str:
    return getpass.getpass(f"    {label} (hidden): ").strip()


def ask(label: str) -> str:
    return input(f"    {label}: ").strip()


def say(ok: bool | None, text: str) -> None:
    print(f"    {MARK[ok]} {text}")


# --- 1. the values ------------------------------------------------------------------

def choose_database() -> str:
    url = migrate_supabase.find_supabase_url() or ""
    if url:
        print(f"    Found in .env: {migrate_supabase.mask(url)}")
    while True:
        if not url:
            print("    In Supabase: your project → Connect → Session pooler → copy the address.")
            print("    Put your database password where it says [YOUR-PASSWORD].")
            url = hidden("Paste the address")
        fixed = session_pooler(url)
        if fixed != url.strip():
            say(None, "That is the Transaction pooler. Using the Session pooler: the same address, port 5432.")
        url = fixed
        problem = database_url_problem(url)
        if problem:
            say(False, problem)
            url = ""
            continue
        ok, summary = asyncio.run(database_summary(url))
        say(ok, summary)
        if ok or (ok is None and ask("Render may reach it all the same. Use it anyway? [y/N]").lower() == "y"):
            return url
        url = ""


def choose_owner_password(current: str) -> str:
    print(f"    Choose one, {MIN_OWNER_PASSWORD} characters or more. Online you sign in with it,")
    print("    the email left empty. Safari offers to save it the first time.")
    while True:
        new = hidden("New owner password")
        problem = owner_password_problem(new, current)
        if problem:
            say(False, problem)
        elif hidden("The same again") != new:
            say(False, "The two did not match.")
        else:
            return new


def working(value: str, check, prompt: str) -> str:
    """The value once its service accepts it (or cannot be asked)."""
    empty = "Not in .env."
    while True:
        ok, message = check(value) if value else (False, empty)
        say(ok, message)
        if ok is not False:
            return value
        value = hidden(prompt)
        empty = "Nothing was pasted."


def choose_app_secret(token: str, known: str) -> tuple[str | None, str]:
    """Meta's app id and App secret, the secret checked with Meta."""
    app_id = meta_app_id(token)
    typed_id = False
    secret = known
    if not secret:
        print("    Meta for Developers → My Apps → your app → App settings → Basic →")
        print("    App secret → Show → copy.")
    if not app_id:
        app_id = ask("The App ID (the number at the top of that page)") or None
        typed_id = True
    while True:
        if not secret:
            secret = hidden("Paste the App secret")
            if not secret:
                say(False, "Nothing was pasted.")
                continue
        verdict = secret_is_the_apps(app_id, secret) if app_id else None
        if verdict is not False:
            say(verdict, "Meta confirms it is the app's secret." if verdict
                else "Could not ask Meta about it; using it as it is.")
            return app_id, secret
        if known and secret == known:
            say(False, "The App secret in .env is not this app's.")
            print("    Meta for Developers → My Apps → your app → App settings → Basic →")
            print("    App secret → Show → copy.")
        else:
            say(False, "Meta says that is not this app's secret. Click Show, copy it again.")
        if typed_id:
            app_id = ask("The App ID again (top of the page)") or app_id
        secret = ""


def lasting_token(token: str, app_id: str, secret: str, phone_id: str) -> str:
    """The WhatsApp token, or a permanent one when it would expire."""
    while True:
        expires = token_expiry(token, app_id, secret)
        if expires is None:
            return token
        if expires == 0:
            say(True, "The WhatsApp token never expires.")
            return token
        when = datetime.fromtimestamp(expires).strftime("%d/%m/%Y %H:%M")
        say(None, f"The WhatsApp token stops working on {when}, and the bot with it.")
        print("    A permanent one: business.facebook.com → Settings → System users → bookingbot")
        print("    → Generate token, expiry Never, with whatsapp_business_messaging and")
        print("    whatsapp_business_management.")
        new = hidden("Paste it, or press Enter to go on with this one")
        if not new:
            return token
        ok, message = configure.check_whatsapp(new, phone_id)
        say(ok, message)
        if ok is not False:
            token = new


def gather(lines: list[str]) -> tuple[dict[str, str], str | None]:
    """Every value Render asks for, checked. And the Meta app's id."""
    values = {key: configure.get(lines, key) for key in ASKED}
    phone_id = values["WHATSAPP_PHONE_ID"]

    print("\n1/5 The online database")
    values["DATABASE_URL"] = choose_database()

    print("\n2/5 A new owner password")
    values["ADMIN_PASSWORD"] = choose_owner_password(values["ADMIN_PASSWORD"])

    print("\n3/5 The OpenAI key in .env")
    values["OPENAI_API_KEY"] = working(values["OPENAI_API_KEY"], configure.check_openai,
                                       "Paste a working key (platform.openai.com/api-keys)")

    print("\n4/5 The WhatsApp token in .env")
    values["WHATSAPP_TOKEN"] = working(values["WHATSAPP_TOKEN"],
                                       lambda token: configure.check_whatsapp(token, phone_id),
                                       "Paste a working WhatsApp token")
    if not values["WHATSAPP_VERIFY_TOKEN"]:
        values["WHATSAPP_VERIFY_TOKEN"] = secrets.token_urlsafe(16)
        configure.save(configure.put(lines, "WHATSAPP_VERIFY_TOKEN", values["WHATSAPP_VERIFY_TOKEN"]))
        say(None, "There was no webhook verify token in .env; made one and saved it there.")

    print("\n5/5 Meta's App secret")
    app_id, values["WHATSAPP_APP_SECRET"] = choose_app_secret(values["WHATSAPP_TOKEN"],
                                                              values["WHATSAPP_APP_SECRET"])
    if app_id:
        values["WHATSAPP_TOKEN"] = lasting_token(values["WHATSAPP_TOKEN"], app_id,
                                                 values["WHATSAPP_APP_SECRET"], phone_id)
    return values, app_id


# --- 2. Render ------------------------------------------------------------------------

def paste_into_render(values: dict[str, str]) -> None:
    print("\nAll checked. Now Render: render.com → New → Blueprint → booking-bot → Connect.")
    print("Blueprint Name: booking-bot. Below it are boxes for the values. Each value here")
    print("is copied for you: click its box, press Cmd+V, come back here and press Enter.\n")
    try:
        for n, key in enumerate(ASKED, 1):
            if values[key]:
                copy(values[key])
                input(f"    {n}/{len(ASKED)} {key}: copied. Paste it, then press Enter. ")
            else:
                input(f"    {n}/{len(ASKED)} {key}: leave it empty. Press Enter. ")
    finally:
        try:
            copy("")  # nothing secret left on the clipboard
        except Exception:
            pass


# --- 3. the server ------------------------------------------------------------------

def make_sure_a_clinic_answers(base: str, password: str, phone_id: str) -> bool:
    token = None
    for _ in range(3):
        if not password:
            password = hidden("Your new owner password, to look at the clinics")
        token, status = sign_in(base, password)
        if token:
            break
        if status == 429:
            say(False, "Too many wrong passwords. Wait a few minutes and run this again.")
            return False
        say(False, "The server refused that password (ADMIN_PASSWORD on Render).")
        password = ""
    if not token:
        return False
    say(True, "Signing in works.")

    clinics = online_clinics(base, token)
    clinic = who_answers(clinics, phone_id)
    if clinic:
        say(True, f"{clinic['name']} answers WhatsApp number ID {phone_id}.")
        return True
    active = [clinic for clinic in clinics if clinic.get("is_active")]
    if not active:
        say(None, "There is no clinic online to answer WhatsApp yet.")
        print(f"    Sign in at {base}, add your clinic on the Clinics page,")
        print(f"    and give it WhatsApp number ID {phone_id}.")
        return False

    print(f"    No clinic answers WhatsApp number ID {phone_id} yet. Which one should?")
    for n, clinic in enumerate(active, 1):
        number = clinic.get("whatsapp_phone_id")
        print(f"      {n}. {clinic['name']}" + (f" (now {number})" if number else ""))
    while True:
        choice = ask("Its number, or press Enter to leave it for now")
        if not choice:
            print(f"    Later: Clinics page → Edit → WhatsApp number ID {phone_id}.")
            return False
        if choice.isdigit() and 1 <= int(choice) <= len(active):
            break
        say(False, f"A number from 1 to {len(active)}, please.")
    clinic = active[int(choice) - 1]
    done, problem = give_number(base, token, clinic["id"], phone_id)
    say(done, f"{clinic['name']} answers WhatsApp number ID {phone_id}." if done else f"Could not: {problem}")
    return done


def finish(base: str, values: dict[str, str], app_id: str | None, wait_minutes: float) -> int:
    verify, phone_id = values["WHATSAPP_VERIFY_TOKEN"], values["WHATSAPP_PHONE_ID"]
    secret = values["WHATSAPP_APP_SECRET"]
    print(f"\nThe server: {base}")
    if not wait_until_up(base, wait_minutes):
        say(False, "It does not answer. On Render, the service's Events and Logs say why.")
        print(f"    Once it shows Live:  ./scripts/go_online.sh {base}")
        return 1
    say(True, "It is up.")

    if not holds_verify_token(base, verify):
        say(False, "Its WHATSAPP_VERIFY_TOKEN is not the one in .env.")
        print("    On Render: booking-bot → Environment → correct it → Save. Then run this again.")
        return 1
    say(True, "It has the same verify token as .env.")

    if not app_id:
        app_id, secret = choose_app_secret(values["WHATSAPP_TOKEN"], secret)
    if not accepts(base, secret):
        say(False, "It refuses messages signed with Meta's App secret: WHATSAPP_APP_SECRET on Render is not it.")
        print("    On Render: booking-bot → Environment → correct it → Save. Then run this again.")
        return 1
    if accepts(base, None):
        say(False, "It also takes unsigned messages, which anyone could fake: WHATSAPP_APP_SECRET is empty on Render.")
        print("    On Render: booking-bot → Environment → fill it in → Save. Then run this again.")
        return 1
    say(True, "It takes only messages signed by Meta.")

    callback = f"{base}/api/v1/webhook"
    pointed, message = point_webhook(app_id, secret, callback, verify) if app_id else (False, "No Meta App ID.")
    say(pointed, message)
    if not pointed:
        print("    By hand instead: Meta for Developers → your app → WhatsApp → Configuration →")
        print("    Webhook → Edit:")
        print(f"      Callback URL  {callback}")
        print(f"      Verify token  {verify}")
        print("    Verify and save, then make sure 'messages' is subscribed below it.")

    answered = make_sure_a_clinic_answers(base, values["ADMIN_PASSWORD"], phone_id)

    if pointed and answered:
        print(f"\nOnline. Send the bot a WhatsApp message to try it. The dashboard: {base}")
        print("(the email left empty, your new owner password). start.sh on this Mac is no")
        print("longer needed for the bot.\n")
        return 0
    print("\nAlmost there: do what is marked above, then send the bot a WhatsApp message.\n")
    return 1


# --- the two ways in -----------------------------------------------------------------

def start_to_finish(allow_http: bool = False) -> int:
    if not shutil.which("pbcopy"):
        print("This needs a Mac: it puts each value on the clipboard with pbcopy.")
        return 1
    lines = configure.load()
    print("\nGoing online. First every value Render needs is checked, then you paste them")
    print("into Render one by one. Typing is hidden, and nothing you type is saved.")
    values, app_id = gather(lines)
    paste_into_render(values)

    print("\nNow press Deploy Blueprint on Render. Then click booking-bot on the page it")
    print("shows: the address is at the top, like https://booking-bot-xxxx.onrender.com.")
    print("Paste it here as soon as you see it; this waits for the server to start.")
    while True:
        address = ask("The address (or press Enter to stop for now)")
        if not address:
            print("\nOnce Render shows it Live, finish with:  ./scripts/go_online.sh https://ADDRESS\n")
            return 0
        base = server_address(address, allow_http)
        if base:
            return finish(base, values, app_id, WAIT_MINUTES)
        say(False, "That is not a web address. Copy it from the top of the service's page (https://...).")


def finish_later(address: str, allow_http: bool = False) -> int:
    base = server_address(address, allow_http)
    if not base:
        print("That is not a web address. Copy it from the top of the service's page on Render (https://...).")
        return 1
    values = {key: configure.get(configure.load(), key) for key in ASKED}
    values["ADMIN_PASSWORD"] = ""  # the one in .env is not the one online; asked for when needed
    return finish(base, values, None, wait_minutes=2)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Put the bot online on Render")
    parser.add_argument("address", nargs="?",
                        help="the server's address on Render: only check it and connect WhatsApp")
    parser.add_argument("--allow-http", action="store_true", help=argparse.SUPPRESS)  # trying it locally
    args = parser.parse_args(argv)
    try:
        if args.address:
            return finish_later(args.address, args.allow_http)
        return start_to_finish(args.allow_http)
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Run it again to start over.")
        return 1
    except httpx.HTTPError as e:
        print(f"\nLost the connection to the server ({type(e).__name__}). Run it again.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
