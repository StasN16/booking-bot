"""
Tests for scripts/go_online.py, the helper that puts the bot online.

Supabase, Meta and Render are faked, and so are the keyboard and the
clipboard. The webhook checks also run against the real server code, so
the helper and the server agree on what a message from Meta looks like.
"""
import importlib.util
import pathlib
import re
from types import SimpleNamespace

import httpx
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


go_online = load("go_online")

SESSION_POOLER = ("postgresql://postgres.abcdef:Secret123@"
                  "aws-1-eu-central-1.pooler.supabase.com:5432/postgres")
PHONE_ID = "1072796699244200"
MAIN_CLINIC = "550e8400-e29b-41d4-a716-446655440000"
BASE = "https://booking-bot-x1y2.onrender.com"
NEW_OWNER = "a-new-long-one"
APP_SECRET = "the-app-secret"


class FakeResponse:
    def __init__(self, status, payload=None):
        self.status_code = status
        self.payload = payload

    def json(self):
        if self.payload is None:
            raise ValueError("not JSON")
        return self.payload


def clinic(id_, name, number=None, active=True, home=False):
    return {"id": id_, "name": name, "whatsapp_phone_id": number,
            "is_active": active, "is_home": home}


# --- what Render asks for ---------------------------------------------------------

class TestWhatRenderAsksFor:
    def test_the_values_render_yaml_leaves_to_the_person_in_its_order(self):
        asked, key = [], None
        for line in (ROOT / "render.yaml").read_text().splitlines():
            match = re.match(r"\s*- key: (\w+)", line)
            if match:
                key = match.group(1)
            elif line.strip() == "sync: false":
                asked.append(key)
        assert asked == go_online.ASKED

    def test_each_is_a_setting_the_server_reads(self):
        from app.config import Settings
        assert set(go_online.ASKED) <= set(Settings.model_fields)


# --- checks that need no network ----------------------------------------------------

class TestDatabaseAddress:
    @pytest.mark.parametrize("url", [
        SESSION_POOLER,
        SESSION_POOLER.replace("postgresql://", "postgresql+asyncpg://"),
        SESSION_POOLER.replace("postgresql://", "postgres://"),
        SESSION_POOLER.replace("Secret123", "p%40ss%23word"),  # already written the address way
        f"  {SESSION_POOLER}\n",
    ])
    def test_a_good_address_passes(self, url):
        assert go_online.database_url_problem(url) is None

    @pytest.mark.parametrize("url, words", [
        ("", "Nothing"),
        (SESSION_POOLER.replace("Secret123", "[YOUR-PASSWORD]"), "[YOUR-PASSWORD]"),
        ("postgresql+asyncpg://postgres.PROJECT_REF:PASSWORD@"
         "aws-1-eu-central-1.pooler.supabase.com:5432/postgres", "example"),
        ("postgresql://postgres:Secret123@db.abcdef.supabase.co:5432/postgres", "Direct connection"),
        ("https://abcdef.supabase.co", "not a database address"),
        ("postgresql://postgres.abcdef@aws-1-eu-central-1.pooler.supabase.com:5432/postgres",
         "no password"),
        (SESSION_POOLER + "?sslmode=require", "the ?"),
        ("postgresql+asyncpg://stas@localhost:5432/bookingbot", "this Mac"),
        (SESSION_POOLER.replace(":5432/", ":port/"), "does not read right"),
    ])
    def test_a_problem_is_named(self, url, words):
        assert words in go_online.database_url_problem(url)

    @pytest.mark.parametrize("password", ["pa@ss", "pa#ss", "pa/ss", "pa?ss", "pa ss"])
    def test_a_password_an_address_cannot_hold(self, password):
        """The server would read such an address wrongly, and fail to start."""
        problem = go_online.database_url_problem(SESSION_POOLER.replace("Secret123", password))
        assert "Reset database password" in problem
        assert password not in problem

    def test_the_transaction_pooler_becomes_the_session_pooler(self):
        assert go_online.session_pooler(SESSION_POOLER.replace(":5432/", ":6543/")) == SESSION_POOLER

    @pytest.mark.parametrize("url", [
        SESSION_POOLER, "postgresql://u:p@example.com:6543/db", "not an address", "",
    ])
    def test_any_other_address_is_left_alone(self, url):
        assert go_online.session_pooler(url) == url


class TestOwnerPassword:
    def test_a_short_one_is_refused(self):
        assert str(go_online.MIN_OWNER_PASSWORD) in go_online.owner_password_problem("short", "")

    def test_the_one_shared_in_a_chat_is_refused(self):
        assert "shared" in go_online.owner_password_problem("old-shared-one", "old-shared-one")

    def test_a_new_long_one_is_fine(self):
        assert go_online.owner_password_problem(NEW_OWNER, "old-shared-one") is None


class TestServerAddress:
    @pytest.mark.parametrize("pasted", [
        BASE, BASE + "/", BASE + "/dashboard/#/calendar", "  booking-bot-x1y2.onrender.com ",
    ])
    def test_the_base_is_found_in_whatever_was_pasted(self, pasted):
        assert go_online.server_address(pasted) == BASE

    @pytest.mark.parametrize("pasted", ["", "http://booking-bot-x1y2.onrender.com", "ftp://x.com", "https://", "oops"])
    def test_anything_else_is_refused(self, pasted):
        assert go_online.server_address(pasted) is None

    def test_plain_http_only_to_try_it_on_this_machine(self):
        assert go_online.server_address("http://127.0.0.1:8000", allow_http=True) == "http://127.0.0.1:8000"


class TestWhoAnswers:
    """The same choice the server makes in app/core/tenancy.py."""

    def test_the_clinic_with_the_number(self):
        clinics = [clinic("a", "A"), clinic("b", "B", PHONE_ID)]
        assert go_online.who_answers(clinics, PHONE_ID)["id"] == "b"

    def test_a_clinic_with_the_number_comes_before_the_main_clinic(self):
        clinics = [clinic("home", "Home", home=True), clinic("b", "B", PHONE_ID)]
        assert go_online.who_answers(clinics, PHONE_ID)["id"] == "b"

    def test_otherwise_the_main_clinic_answers_the_servers_number(self):
        clinics = [clinic("a", "A", "999999"), clinic("home", "Home", home=True)]
        assert go_online.who_answers(clinics, PHONE_ID)["id"] == "home"

    def test_a_clinic_turned_off_does_not_answer(self):
        clinics = [clinic("b", "B", PHONE_ID, active=False), clinic("home", "Home", active=False, home=True)]
        assert go_online.who_answers(clinics, PHONE_ID) is None

    def test_nobody(self):
        assert go_online.who_answers([], PHONE_ID) is None
        assert go_online.who_answers([clinic("a", "A", home=True)], "") is None


# --- the server's side, run for real ------------------------------------------------

class TestTheServerAgrees:
    """The helper's checks, against the real webhook and health endpoints."""

    BASE = "http://testserver"

    @pytest.fixture
    def server(self, monkeypatch):
        from fastapi.testclient import TestClient
        from app.config import settings
        from app.main import app

        monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", APP_SECRET)
        monkeypatch.setattr(settings, "WHATSAPP_VERIFY_TOKEN", "verify-me")
        client = TestClient(app)
        # The helper's timeouts are for the internet; the test client has no use for them.
        monkeypatch.setattr(httpx, "get", lambda url, timeout=None, **kwargs: client.get(url, **kwargs))
        monkeypatch.setattr(httpx, "post", lambda url, timeout=None, **kwargs: client.post(url, **kwargs))
        return settings

    def test_a_post_signed_with_the_app_secret_is_taken(self, server):
        assert go_online.accepts(self.BASE, APP_SECRET)

    def test_one_signed_with_another_secret_is_refused(self, server):
        assert not go_online.accepts(self.BASE, "not-the-secret")

    def test_an_unsigned_one_is_refused(self, server):
        assert not go_online.accepts(self.BASE, None)

    def test_an_unsigned_one_is_taken_by_a_server_without_the_secret(self, server, monkeypatch):
        monkeypatch.setattr(server, "WHATSAPP_APP_SECRET", "")
        assert go_online.accepts(self.BASE, None)

    def test_the_verify_token(self, server):
        assert go_online.holds_verify_token(self.BASE, "verify-me")
        assert not go_online.holds_verify_token(self.BASE, "another-one")

    def test_health(self, server):
        assert go_online.wait_until_up(self.BASE, minutes=0)


# --- Meta, faked ------------------------------------------------------------------------

class TestMeta:
    def test_the_app_is_found_from_the_whatsapp_token(self, monkeypatch):
        seen = {}

        def get(url, **kwargs):
            seen.update(url=url, **kwargs)
            return FakeResponse(200, {"id": "4242", "name": "Booking"})
        monkeypatch.setattr(httpx, "get", get)
        assert go_online.meta_app_id("EAAG") == "4242"
        assert seen["url"].endswith("/app")
        assert seen["headers"]["Authorization"] == "Bearer EAAG"

    @pytest.mark.parametrize("answer", [FakeResponse(400, {"error": {}}), FakeResponse(200), httpx.ConnectError("x")])
    def test_no_app_when_meta_refuses_or_cannot_be_reached(self, monkeypatch, answer):
        def get(url, **kwargs):
            if isinstance(answer, Exception):
                raise answer
            return answer
        monkeypatch.setattr(httpx, "get", get)
        assert go_online.meta_app_id("EAAG") is None

    @pytest.mark.parametrize("answer, verdict", [
        (FakeResponse(200, {"access_token": "4242|x", "token_type": "bearer"}), True),
        (FakeResponse(400, {"error": {"message": "Error validating client secret."}}), False),
        (FakeResponse(503, {}), None),
        (httpx.ConnectError("offline"), None),
    ])
    def test_the_app_secret_check(self, monkeypatch, answer, verdict):
        seen = {}

        def get(url, **kwargs):
            seen.update(url=url, **kwargs)
            if isinstance(answer, Exception):
                raise answer
            return answer
        monkeypatch.setattr(httpx, "get", get)
        assert go_online.secret_is_the_apps("4242", APP_SECRET) is verdict
        assert seen["params"] == {"client_id": "4242", "client_secret": APP_SECRET,
                                  "grant_type": "client_credentials"}

    def test_pointing_the_webhook(self, monkeypatch):
        seen = {}

        def post(url, **kwargs):
            seen.update(url=url, **kwargs)
            return FakeResponse(200, {"success": True})
        monkeypatch.setattr(httpx, "post", post)
        done, _ = go_online.point_webhook("4242", APP_SECRET, BASE + "/api/v1/webhook", "verify-me")
        assert done
        assert seen["url"].endswith("/4242/subscriptions")
        assert seen["data"] == {
            "object": "whatsapp_business_account",
            "callback_url": BASE + "/api/v1/webhook",
            "verify_token": "verify-me",
            "fields": "messages",
            "access_token": f"4242|{APP_SECRET}",
        }

    def test_when_meta_will_not_point_it_the_reason_is_shown(self, monkeypatch):
        payload = {"error": {"message": "The URL couldn't be validated."}}
        monkeypatch.setattr(httpx, "post", lambda url, **kwargs: FakeResponse(400, payload))
        done, message = go_online.point_webhook("4242", APP_SECRET, BASE, "verify-me")
        assert not done and "couldn't be validated" in message
        assert APP_SECRET not in message

    @pytest.mark.parametrize("answer, expiry", [
        (FakeResponse(200, {"data": {"expires_at": 0, "is_valid": True}}), 0),
        (FakeResponse(200, {"data": {"expires_at": 1767225600, "is_valid": True}}), 1767225600),
        (FakeResponse(400, {"error": {"message": "Invalid OAuth access token."}}), None),
        (FakeResponse(200), None),
    ])
    def test_when_the_token_expires(self, monkeypatch, answer, expiry):
        monkeypatch.setattr(httpx, "get", lambda url, **kwargs: answer)
        assert go_online.token_expiry("EAAG", "4242", APP_SECRET) == expiry


class TestWaitingForTheServer:
    def clock(self):
        now = [0.0]

        def sleep(seconds):
            now[0] += seconds
        return now, sleep, lambda: now[0]

    def test_it_waits_until_the_server_answers(self, monkeypatch):
        answers = iter([httpx.ConnectError("not yet"), FakeResponse(502),
                        FakeResponse(200, {"status": "healthy"})])

        def get(url, **kwargs):
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return answer
        monkeypatch.setattr(httpx, "get", get)
        now, sleep, clock = self.clock()
        assert go_online.wait_until_up(BASE, minutes=5, every=10, sleep=sleep, clock=clock)
        assert now[0] == 20

    def test_it_gives_up_in_the_end(self, monkeypatch):
        monkeypatch.setattr(httpx, "get", lambda url, **kwargs: FakeResponse(404))
        now, sleep, clock = self.clock()
        assert not go_online.wait_until_up(BASE, minutes=1, every=10, sleep=sleep, clock=clock)
        assert now[0] == 60


# --- the whole run, on a pretend Mac -------------------------------------------------------

ENV = (
    "DATABASE_URL=postgresql+asyncpg://stas@localhost:5432/bookingbot\n"
    f"# DATABASE_URL (previous): {SESSION_POOLER}\n"
    "ADMIN_PASSWORD=old-shared-one\n"
    "OPENAI_API_KEY=sk-test\n"
    "WHATSAPP_TOKEN=EAAG-token\n"
    "WHATSAPP_VERIFY_TOKEN=verify-me\n"
    f"WHATSAPP_PHONE_ID={PHONE_ID}\n"
    f"BUSINESS_ID={MAIN_CLINIC}\n"
    "CLINIC_START_PASSWORD=Start-2026\n"
    "JWT_SECRET=local-only\n"
)

GATHERED = {
    "DATABASE_URL": SESSION_POOLER,
    "ADMIN_PASSWORD": NEW_OWNER,
    "OPENAI_API_KEY": "sk-test",
    "WHATSAPP_TOKEN": "EAAG-token",
    "WHATSAPP_VERIFY_TOKEN": "verify-me",
    "WHATSAPP_APP_SECRET": APP_SECRET,
    "WHATSAPP_PHONE_ID": PHONE_ID,
    "BUSINESS_ID": MAIN_CLINIC,
    "CLINIC_START_PASSWORD": "Start-2026",
}


@pytest.fixture
def mac(tmp_path, monkeypatch):
    """A .env, a clipboard and a keyboard, and the services the values are tried on."""
    env = tmp_path / ".env"
    env.write_text(ENV)
    monkeypatch.setattr(go_online.configure, "ENV", env)
    monkeypatch.setattr(go_online.migrate_supabase, "ENV", env)

    state = SimpleNamespace(
        env=env, clipboard=[], prompts=[], answers={},
        database=(True, "Connected. Its clinics: Clinic."),
        openai_keys={"sk-test", "sk-new"}, whatsapp_tokens={"EAAG-token", "EAAG-permanent"},
        app_id="4242", expires=0,
    )

    def keyboard(prompt=""):
        state.prompts.append(prompt)
        for words, queue in state.answers.items():
            if words in prompt and queue:
                return queue.pop(0)
        return ""
    monkeypatch.setattr(go_online, "hidden", keyboard)
    monkeypatch.setattr("builtins.input", keyboard)
    monkeypatch.setattr(go_online, "copy", state.clipboard.append)

    async def database_summary(url):
        state.summarized = url
        return state.database
    monkeypatch.setattr(go_online, "database_summary", database_summary)
    monkeypatch.setattr(go_online.configure, "check_openai",
                        lambda key: (key in state.openai_keys, "OpenAI said so"))
    monkeypatch.setattr(go_online.configure, "check_whatsapp",
                        lambda token, phone_id: (token in state.whatsapp_tokens, "Meta said so"))
    monkeypatch.setattr(go_online, "meta_app_id", lambda token: state.app_id)
    monkeypatch.setattr(go_online, "secret_is_the_apps",
                        lambda app_id, secret: app_id == "4242" and secret == APP_SECRET)
    monkeypatch.setattr(go_online, "token_expiry", lambda token, app_id, secret: (
        0 if token == "EAAG-permanent" else state.expires))
    return state


class TestGathering:
    def answer(self, mac, **answers):
        mac.answers.update({words: list(queue) for words, queue in answers.items()})

    def gather(self):
        return go_online.gather(go_online.configure.load())

    def typical(self, mac):
        self.answer(mac, **{"New owner password": [NEW_OWNER], "The same again": [NEW_OWNER],
                            "App secret": [APP_SECRET]})

    def test_three_answers_and_everything_else_comes_from_env(self, mac):
        self.typical(mac)
        values, app_id = self.gather()
        assert values == GATHERED and app_id == "4242"
        assert len(mac.prompts) == 3

    def test_nothing_typed_is_saved(self, mac):
        self.typical(mac)
        self.gather()
        assert mac.env.read_text() == ENV

    def test_the_supabase_address_kept_in_env_is_used_and_checked(self, mac):
        self.typical(mac)
        self.gather()
        assert mac.summarized == SESSION_POOLER

    def test_without_one_in_env_it_is_asked_for(self, mac):
        mac.env.write_text(ENV.replace(f"# DATABASE_URL (previous): {SESSION_POOLER}\n", ""))
        self.typical(mac)
        self.answer(mac, **{"Paste the address": [SESSION_POOLER.replace(":5432/", ":6543/")]})
        values, _ = self.gather()
        assert values["DATABASE_URL"] == SESSION_POOLER  # moved to the Session pooler

    def test_an_address_supabase_refuses_is_asked_for_again(self, mac, monkeypatch):
        good = SESSION_POOLER.replace("Secret123", "NewSecret456")

        async def database_summary(url):
            return (True, "Connected.") if url == good else (False, "Supabase refused the password.")
        monkeypatch.setattr(go_online, "database_summary", database_summary)
        self.typical(mac)
        self.answer(mac, **{"Paste the address": [good]})
        values, _ = self.gather()
        assert values["DATABASE_URL"] == good

    def test_an_address_out_of_reach_from_here_can_still_be_used(self, mac):
        mac.database = (None, "Could not reach it from this network (TimeoutError).")
        self.typical(mac)
        self.answer(mac, **{"Use it anyway": ["y"]})
        values, _ = self.gather()
        assert values["DATABASE_URL"] == SESSION_POOLER

    def test_the_owner_password_must_be_new_and_typed_twice_alike(self, mac):
        self.answer(mac, **{"New owner password": ["old-shared-one", "short", NEW_OWNER, NEW_OWNER],
                            "The same again": ["a-typo-in-it", NEW_OWNER],
                            "App secret": [APP_SECRET]})
        values, _ = self.gather()
        assert values["ADMIN_PASSWORD"] == NEW_OWNER

    def test_a_rejected_openai_key_is_replaced(self, mac):
        mac.openai_keys = {"sk-new"}
        self.typical(mac)
        self.answer(mac, **{"working key": ["sk-new"]})
        values, _ = self.gather()
        assert values["OPENAI_API_KEY"] == "sk-new"

    def test_a_wrong_app_secret_is_asked_for_again(self, mac):
        self.typical(mac)
        self.answer(mac, **{"App secret": ["a-wrong-one", APP_SECRET]})
        values, _ = self.gather()
        assert values["WHATSAPP_APP_SECRET"] == APP_SECRET

    def test_an_app_secret_already_in_env_is_used_once_meta_confirms_it(self, mac):
        mac.env.write_text(ENV + f"WHATSAPP_APP_SECRET={APP_SECRET}\n")
        self.answer(mac, **{"New owner password": [NEW_OWNER], "The same again": [NEW_OWNER]})
        values, _ = self.gather()
        assert values["WHATSAPP_APP_SECRET"] == APP_SECRET
        assert not any("App secret" in prompt for prompt in mac.prompts)

    def test_the_app_id_is_asked_for_when_the_token_does_not_say(self, mac):
        mac.app_id = None
        self.typical(mac)
        self.answer(mac, **{"App ID": ["4242"]})
        _, app_id = self.gather()
        assert app_id == "4242"

    def test_a_token_that_expires_can_be_swapped_for_a_permanent_one(self, mac):
        mac.expires = 1767225600
        self.typical(mac)
        self.answer(mac, **{"Paste it, or press Enter": ["EAAG-permanent"]})
        values, _ = self.gather()
        assert values["WHATSAPP_TOKEN"] == "EAAG-permanent"

    def test_or_kept(self, mac, capsys):
        mac.expires = 1767225600
        self.typical(mac)
        values, _ = self.gather()
        assert values["WHATSAPP_TOKEN"] == "EAAG-token"
        assert "stops working" in capsys.readouterr().out

    def test_a_missing_verify_token_is_made_and_saved(self, mac):
        mac.env.write_text(ENV.replace("WHATSAPP_VERIFY_TOKEN=verify-me\n", ""))
        self.typical(mac)
        values, _ = self.gather()
        made = values["WHATSAPP_VERIFY_TOKEN"]
        assert len(made) >= 16
        assert go_online.configure.get(mac.env.read_text().splitlines(), "WHATSAPP_VERIFY_TOKEN") == made

    def test_secrets_never_reach_the_screen(self, mac, capsys):
        self.typical(mac)
        self.gather()
        out = capsys.readouterr().out
        for secret in (NEW_OWNER, APP_SECRET, "Secret123", "sk-test", "EAAG-token"):
            assert secret not in out


class TestPastingIntoRender:
    def test_each_value_is_copied_in_the_order_render_lists_them(self, mac):
        go_online.paste_into_render(GATHERED)
        assert mac.clipboard == [GATHERED[key] for key in go_online.ASKED] + [""]

    def test_an_empty_one_is_left_empty(self, mac):
        go_online.paste_into_render({**GATHERED, "CLINIC_START_PASSWORD": ""})
        assert "" not in mac.clipboard[:-1]
        assert any("CLINIC_START_PASSWORD: leave it empty" in prompt for prompt in mac.prompts)

    def test_the_clipboard_is_emptied_even_when_stopped_halfway(self, mac, monkeypatch):
        def stop(prompt=""):
            raise KeyboardInterrupt
        monkeypatch.setattr("builtins.input", stop)
        with pytest.raises(KeyboardInterrupt):
            go_online.paste_into_render(GATHERED)
        assert mac.clipboard == [SESSION_POOLER, ""]


@pytest.fixture
def render(monkeypatch):
    """The new server and Meta, as the helper sees them."""
    state = SimpleNamespace(
        up=True, verify_token="verify-me", app_secret=APP_SECRET, pointed=(True, "WhatsApp now sends messages."),
        owner_password=NEW_OWNER, clinics=[clinic("c1", "Clinic", PHONE_ID)],
        hooks=[], tried=[], given=[], waited=[],
    )

    def wait_until_up(base, minutes):
        state.waited.append(minutes)
        return state.up
    monkeypatch.setattr(go_online, "wait_until_up", wait_until_up)
    monkeypatch.setattr(go_online, "holds_verify_token", lambda base, verify: verify == state.verify_token)
    monkeypatch.setattr(go_online, "accepts", lambda base, secret: (
        secret == state.app_secret if state.app_secret else True))

    def point_webhook(app_id, secret, callback, verify):
        state.hooks.append((app_id, secret, callback, verify))
        return state.pointed
    monkeypatch.setattr(go_online, "point_webhook", point_webhook)

    def sign_in(base, password):
        state.tried.append(password)
        return ("jwt", 200) if password == state.owner_password else (None, 401)
    monkeypatch.setattr(go_online, "sign_in", sign_in)
    monkeypatch.setattr(go_online, "online_clinics", lambda base, token: state.clinics)

    def give_number(base, token, clinic_id, phone_id):
        state.given.append((clinic_id, phone_id))
        return True, ""
    monkeypatch.setattr(go_online, "give_number", give_number)
    return state


class TestFinishing:
    def finish(self):
        return go_online.finish(BASE, dict(GATHERED), "4242", go_online.WAIT_MINUTES)

    def test_all_well(self, mac, render, capsys):
        assert self.finish() == 0
        assert render.hooks == [("4242", APP_SECRET, BASE + "/api/v1/webhook", "verify-me")]
        assert render.tried == [NEW_OWNER]
        assert "Online." in capsys.readouterr().out
        assert mac.prompts == []  # nothing more to type

    def test_a_server_that_never_comes_up(self, mac, render, capsys):
        render.up = False
        assert self.finish() == 1
        assert f"./scripts/go_online.sh {BASE}" in capsys.readouterr().out
        assert render.hooks == []

    def test_a_different_verify_token_on_render(self, mac, render):
        render.verify_token = "a-typo"
        assert self.finish() == 1
        assert render.hooks == []

    def test_a_different_app_secret_on_render(self, mac, render, capsys):
        render.app_secret = "another-secret"
        assert self.finish() == 1
        assert "WHATSAPP_APP_SECRET on Render" in capsys.readouterr().out
        assert render.hooks == []

    def test_no_app_secret_on_render_is_not_let_through(self, mac, render, capsys):
        """Anyone could send it fake messages: WhatsApp is not pointed at it."""
        render.app_secret = ""
        assert self.finish() == 1
        assert "empty on Render" in capsys.readouterr().out
        assert render.hooks == []

    def test_when_meta_will_not_point_the_webhook_the_way_by_hand_is_shown(self, mac, render, capsys):
        render.pointed = (False, "Meta did not take it: The URL couldn't be validated.")
        assert self.finish() == 1
        out = capsys.readouterr().out
        assert f"Callback URL  {BASE}/api/v1/webhook" in out
        assert "Verify token  verify-me" in out

    def test_no_clinic_with_the_number_one_is_chosen(self, mac, render):
        render.clinics = [clinic("c1", "First"), clinic("c2", "Second", "555555"),
                          clinic("c3", "Off", active=False)]
        mac.answers["Its number"] = ["7", "2"]
        assert self.finish() == 0
        assert render.given == [("c2", PHONE_ID)]

    def test_or_left_for_later(self, mac, render, capsys):
        render.clinics = [clinic("c1", "First")]
        assert self.finish() == 1
        assert render.given == []
        assert f"WhatsApp number ID {PHONE_ID}" in capsys.readouterr().out

    def test_no_clinic_online_yet(self, mac, render, capsys):
        render.clinics = []
        assert self.finish() == 1
        out = capsys.readouterr().out
        assert "add your clinic on the Clinics page" in out and PHONE_ID in out

    def test_the_main_clinic_answers_the_servers_number(self, mac, render):
        render.clinics = [clinic(MAIN_CLINIC, "Main", home=True)]
        assert self.finish() == 0


class TestTheTwoWaysIn:
    def test_start_to_finish(self, mac, render, monkeypatch):
        monkeypatch.setattr(go_online.shutil, "which", lambda name: "/usr/bin/pbcopy")
        mac.answers.update({"New owner password": [NEW_OWNER], "The same again": [NEW_OWNER],
                            "App secret": [APP_SECRET], "The address": ["oops", "booking-bot-x1y2.onrender.com"]})
        assert go_online.main([]) == 0
        assert mac.clipboard == [GATHERED[key] for key in go_online.ASKED] + [""]
        assert render.hooks[0][2] == BASE + "/api/v1/webhook"
        assert render.waited == [go_online.WAIT_MINUTES]
        assert render.tried == [NEW_OWNER]  # signed in with the password just chosen, untyped

    def test_stopping_before_the_address_says_how_to_finish(self, mac, render, monkeypatch, capsys):
        monkeypatch.setattr(go_online.shutil, "which", lambda name: "/usr/bin/pbcopy")
        mac.answers.update({"New owner password": [NEW_OWNER], "The same again": [NEW_OWNER],
                            "App secret": [APP_SECRET]})
        assert go_online.main([]) == 0
        assert "./scripts/go_online.sh https://ADDRESS" in capsys.readouterr().out
        assert render.hooks == []

    def test_only_on_a_mac(self, mac, monkeypatch, capsys):
        monkeypatch.setattr(go_online.shutil, "which", lambda name: None)
        assert go_online.main([]) == 1
        assert "Mac" in capsys.readouterr().out

    def test_finishing_later_asks_for_the_online_password_not_the_one_in_env(self, mac, render):
        mac.answers.update({"App secret": [APP_SECRET], "owner password": [NEW_OWNER]})
        assert go_online.main([BASE + "/dashboard/"]) == 0
        assert render.tried == [NEW_OWNER]
        assert render.hooks == [("4242", APP_SECRET, BASE + "/api/v1/webhook", "verify-me")]

    def test_finishing_later_with_a_bad_address(self, mac, capsys):
        assert go_online.main(["not a web address"]) == 1
        assert "not a web address" in capsys.readouterr().out

    def test_stopping_with_ctrl_c(self, mac, monkeypatch, capsys):
        monkeypatch.setattr(go_online.shutil, "which", lambda name: "/usr/bin/pbcopy")

        def stop(label):
            raise KeyboardInterrupt
        monkeypatch.setattr(go_online, "hidden", stop)
        assert go_online.main([]) == 1
        assert "Stopped" in capsys.readouterr().out

    def test_a_lost_connection(self, mac, render, monkeypatch, capsys):
        def lost(base, verify):
            raise httpx.ReadTimeout("slow")
        monkeypatch.setattr(go_online, "holds_verify_token", lost)
        assert go_online.main([BASE]) == 1
        assert "Lost the connection" in capsys.readouterr().out
