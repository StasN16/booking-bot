"""
Which clinic is being served, and which WhatsApp number it sends from.

Getting either wrong leaks one clinic's customers into another's dashboard
or chat, so the rules are pinned down here.
"""
import asyncio

import pytest

from app.config import settings
from app.core import tenancy


@pytest.fixture(autouse=True)
def clinics(monkeypatch):
    tenancy.forget_channels()
    monkeypatch.setattr(settings, "BUSINESS_ID", "home-clinic")
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_ID", "111")
    monkeypatch.setattr(settings, "WHATSAPP_TOKEN", "server-token")
    tenancy._channels.update({"clinic-b": tenancy.Channel("222", "b-token")})
    tenancy._owners.update({"222": "clinic-b"})
    yield
    tenancy.forget_channels()


class TestCurrentClinic:
    def test_outside_any_request_it_is_the_home_clinic(self):
        assert tenancy.current_business_id() == "home-clinic"

    def test_acting_for_a_clinic_and_back(self):
        with tenancy.acting_for("clinic-b"):
            assert tenancy.current_business_id() == "clinic-b"
            with tenancy.acting_for("clinic-c"):
                assert tenancy.current_business_id() == "clinic-c"
            assert tenancy.current_business_id() == "clinic-b"
        assert tenancy.current_business_id() == "home-clinic"

    def test_an_error_inside_does_not_leave_the_clinic_set(self):
        with pytest.raises(RuntimeError):
            with tenancy.acting_for("clinic-b"):
                raise RuntimeError("boom")
        assert tenancy.current_business_id() == "home-clinic"

    @pytest.mark.asyncio
    async def test_concurrent_work_never_sees_another_clinics_id(self):
        async def serve(clinic):
            with tenancy.acting_for(clinic):
                await asyncio.sleep(0.01)
                return tenancy.current_business_id()

        results = await asyncio.gather(*(serve(f"clinic-{i}") for i in range(20)))
        assert results == [f"clinic-{i}" for i in range(20)]


class TestSendingNumber:
    def test_a_clinic_sends_from_its_own_number(self):
        with tenancy.acting_for("clinic-b"):
            assert tenancy.channel() == tenancy.Channel("222", "b-token")

    def test_the_home_clinic_sends_from_the_number_in_env(self):
        assert tenancy.channel() == tenancy.Channel("111", "server-token")

    def test_a_clinic_without_a_number_sends_nothing(self):
        """Never borrow another clinic's number to message someone."""
        with tenancy.acting_for("clinic-without-number"):
            assert tenancy.channel() is None

    @pytest.mark.asyncio
    async def test_the_message_goes_to_the_clinics_own_number(self, monkeypatch):
        import httpx
        from app.services import whatsapp

        posted = []

        class Response:
            status_code = 200
            text = "ok"

        async def post(self, url, **kwargs):
            posted.append((url, kwargs["headers"]["Authorization"]))
            return Response()

        monkeypatch.setattr(httpx.AsyncClient, "post", post)
        monkeypatch.setattr(settings, "REPLY_DELAY_SECONDS", 0)
        with tenancy.acting_for("clinic-b"):
            assert await whatsapp.send_message("972500000000", "hi") is True
        assert posted == [(f"{whatsapp.WHATSAPP_API_URL}/222/messages", "Bearer b-token")]

    def test_the_home_clinic_turned_off_sends_nothing(self):
        tenancy._off.add("home-clinic")
        assert tenancy.channel() is None

    @pytest.mark.asyncio
    async def test_a_clinic_without_a_number_fails_to_send(self, monkeypatch):
        import httpx
        from app.services import whatsapp

        async def post(self, url, **kwargs):
            raise AssertionError("nothing may be posted")

        monkeypatch.setattr(httpx.AsyncClient, "post", post)
        monkeypatch.setattr(settings, "REPLY_DELAY_SECONDS", 0)
        with tenancy.acting_for("clinic-without-number"):
            assert await whatsapp.send_message("972500000000", "hi") is False


class TestReceivingNumber:
    @pytest.fixture(autouse=True)
    def no_database(self, monkeypatch):
        async def load():
            tenancy._last_refresh = __import__("time").monotonic()
        monkeypatch.setattr(tenancy, "refresh_channels", load)

    @pytest.mark.asyncio
    async def test_a_known_number_finds_its_clinic(self):
        assert await tenancy.business_for_phone_number("222") == "clinic-b"

    @pytest.mark.asyncio
    async def test_the_number_in_env_finds_the_home_clinic(self):
        assert await tenancy.business_for_phone_number("111") == "home-clinic"

    @pytest.mark.asyncio
    async def test_an_unknown_number_finds_nobody(self):
        assert await tenancy.business_for_phone_number("999") is None

    @pytest.mark.asyncio
    async def test_no_number_means_the_home_clinic(self):
        assert await tenancy.business_for_phone_number(None) == "home-clinic"

    @pytest.mark.asyncio
    async def test_the_home_clinic_turned_off_answers_nobody(self):
        """Off means off, even on the number in .env."""
        tenancy._off.add("home-clinic")
        assert await tenancy.business_for_phone_number("111") is None
        assert await tenancy.business_for_phone_number(None) is None


class TestLoadingNumbers:
    """refresh_channels() against a stand-in for the database."""

    @pytest.fixture
    def stored(self, monkeypatch):
        from types import SimpleNamespace
        from app.core import db

        rows = []

        class Result:
            def scalars(self):
                return SimpleNamespace(all=lambda: rows)

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, query):
                return Result()

        monkeypatch.setattr(db, "async_session", Session)

        def add(business_id, phone_id=None, token=None, active=True):
            rows.append(SimpleNamespace(id=business_id, whatsapp_phone_id=phone_id,
                                        whatsapp_token=token, is_active=active))
            return rows[-1]
        return add

    @pytest.mark.asyncio
    async def test_numbers_and_tokens_are_loaded(self, stored):
        stored("clinic-c", phone_id="333", token="c-token")
        stored("clinic-d", phone_id="444")
        await tenancy.refresh_channels()
        assert tenancy.channel("clinic-c") == tenancy.Channel("333", "c-token")
        assert tenancy.channel("clinic-d") == tenancy.Channel("444", "server-token")
        assert await tenancy.business_for_phone_number("444") == "clinic-d"

    @pytest.mark.asyncio
    async def test_a_clinic_turned_off_has_no_number(self, stored):
        stored("clinic-c", phone_id="333", active=False)
        await tenancy.refresh_channels()
        assert tenancy.channel("clinic-c") is None
        assert await tenancy.business_for_phone_number("333") is None

    @pytest.mark.asyncio
    async def test_the_home_clinic_turned_off_loses_the_env_number(self, stored):
        stored("home-clinic", active=False)
        await tenancy.refresh_channels()
        assert tenancy.channel("home-clinic") is None
        assert await tenancy.business_for_phone_number("111") is None

    @pytest.mark.asyncio
    async def test_turned_back_on_it_answers_again(self, stored):
        home = stored("home-clinic", active=False)
        await tenancy.refresh_channels()
        home.is_active = True
        await tenancy.refresh_channels()
        assert tenancy.channel("home-clinic") == tenancy.Channel("111", "server-token")
        assert await tenancy.business_for_phone_number("111") == "home-clinic"
