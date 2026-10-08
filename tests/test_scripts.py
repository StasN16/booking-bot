"""
Tests for the setup scripts' logic.

The scripts live in scripts/, outside any package, so they are loaded by
path. Only the pure parts are tested here; the scripts themselves were run
end to end against a simulated Mac.
"""
import importlib.util
import pathlib

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


setup_local_db = load("setup_local_db")
configure = load("configure")
start_password = load("start_password")

SUPABASE = ("postgresql+asyncpg://postgres.ref:secret@"
            "aws-1-eu-central-1.pooler.supabase.com:5432/postgres")
LOCAL = "postgresql+asyncpg://stas@localhost:5432/bookingbot"


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setattr(setup_local_db, "ENV", path)
    return path


class TestBuildUrl:
    def test_no_password_leaves_it_out(self):
        """Homebrew's PostgreSQL trusts local connections: user, no password."""
        assert setup_local_db.build_url("stas", "", "localhost", "5432") == LOCAL

    def test_password_is_included(self):
        url = setup_local_db.build_url("postgres", "pw", "localhost", "5432")
        assert url == "postgresql+asyncpg://postgres:pw@localhost:5432/bookingbot"

    def test_awkward_password_is_encoded(self):
        url = setup_local_db.build_url("postgres", "p@ss:w/rd", "localhost", "5432")
        assert "p%40ss%3Aw%2Frd" in url
        assert url.count("@") == 1


class TestWriteEnv:
    def test_creates_the_file_when_missing(self, env_file):
        setup_local_db.write_env(LOCAL)
        assert env_file.read_text() == f"DATABASE_URL={LOCAL}\n"

    def test_previous_url_is_kept_as_a_comment(self, env_file):
        env_file.write_text(f"DATABASE_URL={SUPABASE}\nOPENAI_API_KEY=sk-x\n")
        setup_local_db.write_env(LOCAL)
        text = env_file.read_text()
        assert f"DATABASE_URL={LOCAL}" in text
        assert f"{setup_local_db.PREVIOUS_MARKER} {SUPABASE}" in text
        assert "OPENAI_API_KEY=sk-x" in text

    def test_running_twice_keeps_the_supabase_url(self, env_file):
        """The old version dropped it on the second run."""
        env_file.write_text(f"DATABASE_URL={SUPABASE}\n")
        setup_local_db.write_env(LOCAL)
        setup_local_db.write_env(LOCAL)
        assert SUPABASE in env_file.read_text()

    def test_several_earlier_urls_all_survive(self, env_file):
        windows_local = "postgresql+asyncpg://postgres:pw@localhost:5432/bookingbot"
        env_file.write_text(f"# DATABASE_URL (previous): {SUPABASE}\n"
                            f"DATABASE_URL={windows_local}\n")
        setup_local_db.write_env(LOCAL)
        text = env_file.read_text()
        assert SUPABASE in text and windows_local in text
        assert text.count("DATABASE_URL=") == 1

    def test_the_active_url_is_not_also_listed_as_previous(self, env_file):
        env_file.write_text(f"# DATABASE_URL (previous): {LOCAL}\nDATABASE_URL={SUPABASE}\n")
        setup_local_db.write_env(LOCAL)
        assert env_file.read_text().count(LOCAL) == 1

    def test_the_example_placeholder_is_dropped(self, env_file):
        placeholder = ("postgresql+asyncpg://postgres.PROJECT_REF:PASSWORD@"
                       "aws-1-eu-central-1.pooler.supabase.com:5432/postgres")
        env_file.write_text(f"DATABASE_URL={placeholder}\nTIMEZONE=Asia/Jerusalem\n")
        setup_local_db.write_env(LOCAL)
        text = env_file.read_text()
        assert "PROJECT_REF" not in text
        assert "TIMEZONE=Asia/Jerusalem" in text

    def test_other_lines_keep_their_order(self, env_file):
        env_file.write_text(f"A=1\nDATABASE_URL={SUPABASE}\nB=2\n")
        setup_local_db.write_env(LOCAL)
        lines = env_file.read_text().splitlines()
        assert lines[0] == "A=1" and lines[-1] == "B=2"


class TestDefaultUser:
    def test_windows_uses_postgres(self, monkeypatch):
        monkeypatch.setattr(setup_local_db.platform, "system", lambda: "Windows")
        assert setup_local_db.default_user() == "postgres"

    def test_mac_uses_the_account_name(self, monkeypatch):
        monkeypatch.setattr(setup_local_db.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(setup_local_db.getpass, "getuser", lambda: "stas")
        assert setup_local_db.default_user() == "stas"


class TestEnvValues:
    @pytest.mark.parametrize("value", [
        "simple123", "p@ss w#rd", "it's", 'say "hi"', "back\\slash",
        "trailing ", "עברית", "x=y", "$HOME",
    ])
    def test_values_survive_python_dotenv(self, tmp_path, value):
        """What configure writes must read back unchanged in the app."""
        from dotenv import dotenv_values
        path = tmp_path / ".env"
        path.write_text("\n".join(configure.put([], "K", value)) + "\n")
        assert dotenv_values(path)["K"] == value

    def test_put_replaces_and_removes_duplicates(self):
        lines = ["A=1", "K=old", "B=2", "K=older"]
        assert configure.put(lines, "K", "new") == ["A=1", "K=new", "B=2"]

    def test_put_appends_a_missing_key(self):
        assert configure.put(["A=1"], "K", "v") == ["A=1", "K=v"]

    def test_get_reads_the_last_uncommented_value(self):
        lines = ["# K=commented", "K=first", "K='second'"]
        assert configure.get(lines, "K") == "second"

    def test_get_of_a_missing_key_is_empty(self):
        assert configure.get(["A=1"], "K") == ""

    def test_get_does_not_match_a_longer_key(self):
        assert configure.get(["WHATSAPP_TOKEN_OLD=x"], "WHATSAPP_TOKEN") == ""

    def test_dollar_brace_is_refused(self, monkeypatch):
        """python-dotenv would expand it, so it is asked for again."""
        answers = iter(["abc${HOME}def", "fine"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
        assert configure.ask("Label", hidden=False, hint="hint") == "fine"


class TestCredentialChecks:
    """The live checks, with the network faked."""

    def fake_response(self, status, payload):
        class Response:
            status_code = status
            def json(self):
                return payload
        return Response()

    def test_openai_accepts_a_working_key(self, monkeypatch):
        import httpx
        monkeypatch.setattr(httpx, "get", lambda *a, **k: self.fake_response(200, {}))
        ok, message = configure.check_openai("sk-test")
        assert ok is True

    def test_openai_reports_a_rejected_key(self, monkeypatch):
        import httpx
        monkeypatch.setattr(httpx, "get", lambda *a, **k: self.fake_response(401, {}))
        ok, message = configure.check_openai("sk-wrong")
        assert ok is False and "rejected" in message

    def test_whatsapp_reports_the_number_it_works_for(self, monkeypatch):
        import httpx
        payload = {"display_phone_number": "+1 555-139-0508", "verified_name": "Clinic"}
        monkeypatch.setattr(httpx, "get", lambda *a, **k: self.fake_response(200, payload))
        ok, message = configure.check_whatsapp("EAAG", "123")
        assert ok is True and "+1 555-139-0508" in message

    def test_whatsapp_explains_an_expired_token(self, monkeypatch):
        import httpx
        payload = {"error": {"code": 190, "message": "Session has expired"}}
        monkeypatch.setattr(httpx, "get", lambda *a, **k: self.fake_response(401, payload))
        ok, message = configure.check_whatsapp("EAAG", "123")
        assert ok is False and "token" in message

    def test_whatsapp_explains_a_wrong_phone_id(self, monkeypatch):
        import httpx
        payload = {"error": {"code": 100, "message": "Unsupported get request"}}
        monkeypatch.setattr(httpx, "get", lambda *a, **k: self.fake_response(400, payload))
        ok, message = configure.check_whatsapp("EAAG", "999")
        assert ok is False and "phone number ID" in message

    def test_no_network_is_neither_pass_nor_fail(self, monkeypatch):
        import httpx
        def boom(*a, **k):
            raise httpx.ConnectError("offline")
        monkeypatch.setattr(httpx, "get", boom)
        ok, message = configure.check_openai("sk-test")
        assert ok is None and "could not reach" in message

    def test_secrets_never_appear_in_messages(self, monkeypatch):
        import httpx
        monkeypatch.setattr(httpx, "get", lambda *a, **k: self.fake_response(401, {}))
        _, message = configure.check_openai("sk-very-secret-value")
        assert "sk-very-secret-value" not in message


class TestStartPassword:
    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        path = tmp_path / ".env"
        path.write_text("ADMIN_PASSWORD=owner-secret\nCLINIC_START_PASSWORD=old-start-1\n")
        monkeypatch.setattr(start_password.configure, "ENV", path)
        return path

    @pytest.mark.parametrize("password, accepted", [
        ("Start-2026", True), ("short", False), (" Start-2026", False), ("St${HOME}26", False),
    ])
    def test_what_is_accepted(self, password, accepted):
        assert (start_password.problem_with(password) is None) == accepted

    def test_it_is_saved_in_env_and_nothing_else_changes(self, env, capsys):
        assert start_password.main(["Start-2026"]) == 0
        lines = env.read_text().splitlines()
        assert configure.get(lines, "CLINIC_START_PASSWORD") == "Start-2026"
        assert configure.get(lines, "ADMIN_PASSWORD") == "owner-secret"
        assert "Restart the bot" in capsys.readouterr().out

    def test_a_short_one_is_not_saved(self, env):
        before = env.read_text()
        assert start_password.main(["short"]) == 1
        assert env.read_text() == before
