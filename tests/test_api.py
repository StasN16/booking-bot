"""
Tests for the management API that need no database.

Authentication, validation and error mapping are pure logic and are worth
guarding here. The endpoints themselves are exercised against a real
database by scripts/integration_test.py.
"""
from datetime import timedelta

import pytest
from fastapi import HTTPException
from jose import jwt

from app.config import settings
from app.core.timeutils import now as clinic_now
from app.dependencies import (
    ALGORITHM,
    bearer_token,
    check_password,
    current_user,
    issue_token,
)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "correct-horse")
    monkeypatch.setattr(settings, "JWT_HOURS", 12)


class TestPassword:
    def test_the_right_password_is_accepted(self, configured):
        assert check_password("correct-horse") is True

    def test_a_wrong_password_is_rejected(self, configured):
        assert check_password("wrong") is False

    @pytest.mark.parametrize("value", ["", None])
    def test_empty_input_is_rejected(self, configured, value):
        assert check_password(value) is False

    def test_no_configured_password_means_nothing_is_accepted(self, monkeypatch):
        """Missing configuration must close the door, not open it."""
        monkeypatch.setattr(settings, "ADMIN_PASSWORD", "")
        assert check_password("") is False
        assert check_password("anything") is False


class TestTokens:
    def test_a_token_round_trips(self, configured):
        token = issue_token()["access_token"]
        assert current_user(token).role == "owner"

    def test_a_clinic_token_says_which_clinic(self, configured):
        token = issue_token(subject="user:u-1", role="clinic", business_id="clinic-b")["access_token"]
        who = current_user(token)
        assert (who.role, who.user_id, who.business_id) == ("clinic", "u-1", "clinic-b")
        assert not who.is_owner

    def test_an_owner_token_from_before_clinic_logins_still_works(self, configured):
        old = jwt.encode({"sub": "owner", "exp": clinic_now() + timedelta(hours=1)},
                         settings.JWT_SECRET, algorithm=ALGORITHM)
        assert current_user(old).is_owner

    def test_a_clinic_token_without_its_clinic_is_rejected(self, configured):
        token = jwt.encode({"sub": "user:u-1", "role": "clinic", "exp": clinic_now() + timedelta(hours=1)},
                           settings.JWT_SECRET, algorithm=ALGORITHM)
        with pytest.raises(HTTPException) as raised:
            current_user(token)
        assert raised.value.status_code == 401

    def test_a_clinic_cannot_claim_to_be_the_owner(self, configured):
        token = jwt.encode({"sub": "user:u-1", "role": "owner", "exp": clinic_now() + timedelta(hours=1)},
                           settings.JWT_SECRET, algorithm=ALGORITHM)
        with pytest.raises(HTTPException):
            current_user(token)

    def test_the_response_says_when_it_expires(self, configured):
        assert issue_token()["expires_at"]
        assert issue_token()["token_type"] == "bearer"

    def test_a_token_signed_with_another_key_is_rejected(self, configured):
        forged = jwt.encode({"sub": "owner"}, "different-secret",
                            algorithm=ALGORITHM)
        with pytest.raises(HTTPException) as raised:
            current_user(forged)
        assert raised.value.status_code == 401

    def test_an_expired_token_is_rejected(self, configured):
        stale = jwt.encode(
            {"sub": "owner", "exp": clinic_now() - timedelta(hours=1)},
            settings.JWT_SECRET, algorithm=ALGORITHM)
        with pytest.raises(HTTPException) as raised:
            current_user(stale)
        assert raised.value.status_code == 401

    def test_a_token_without_a_subject_is_rejected(self, configured):
        empty = jwt.encode({}, settings.JWT_SECRET, algorithm=ALGORITHM)
        with pytest.raises(HTTPException) as raised:
            current_user(empty)
        assert raised.value.status_code == 401

    @pytest.mark.parametrize("token", ["", "garbage", "a.b.c"])
    def test_nonsense_is_rejected(self, configured, token):
        with pytest.raises(HTTPException):
            current_user(token)

    def test_without_a_secret_the_api_is_closed(self, monkeypatch):
        """No signing key means refuse everything rather than accept anything."""
        monkeypatch.setattr(settings, "JWT_SECRET", "")
        with pytest.raises(HTTPException) as raised:
            current_user("any-token")
        assert raised.value.status_code == 503


class TestAuthorizationHeader:
    def test_a_bearer_header_is_read(self):
        assert bearer_token("Bearer abc123") == "abc123"

    def test_the_scheme_is_case_insensitive(self):
        assert bearer_token("bearer abc123") == "abc123"

    @pytest.mark.parametrize("header", [
        None, "", "abc123", "Basic abc123", "Bearer", "Bearer ",
    ])
    def test_anything_else_is_rejected(self, header):
        with pytest.raises(HTTPException) as raised:
            bearer_token(header)
        assert raised.value.status_code == 401


class TestValidation:
    """
    Availability matches therapists on exact English day names, so a typo
    would silently remove someone from every search. It is rejected instead.
    """

    def test_valid_days_are_normalized(self):
        from app.core.schemas.api import validate_days
        assert validate_days("sunday, MONDAY ,Tuesday") == "Sunday,Monday,Tuesday"

    @pytest.mark.parametrize("bad", ["Funday", "Mon", "יום ראשון", "Sunday,Notaday"])
    def test_unknown_days_are_rejected(self, bad):
        from app.core.schemas.api import validate_days
        with pytest.raises(ValueError):
            validate_days(bad)

    def test_hours_are_normalized(self):
        from app.core.schemas.api import validate_hhmm
        assert validate_hhmm("9:5") == "09:05"
        assert validate_hhmm("09") == "09:00"

    @pytest.mark.parametrize("bad", ["25:00", "09:99", "morning", "abc"])
    def test_impossible_hours_are_rejected(self, bad):
        from app.core.schemas.api import validate_hhmm
        with pytest.raises(ValueError):
            validate_hhmm(bad)

    def test_a_treatment_needs_a_sensible_duration(self):
        from pydantic import ValidationError
        from app.core.schemas.api import TreatmentIn
        with pytest.raises(ValidationError):
            TreatmentIn(name="x", duration_minutes=0, price=100)
        with pytest.raises(ValidationError):
            TreatmentIn(name="x", duration_minutes=60, price=-5)

    def test_a_valid_treatment_passes(self):
        from app.core.schemas.api import TreatmentIn
        assert TreatmentIn(name="Massage", duration_minutes=60, price=280).price == 280


class TestErrorMapping:
    """A booking rule broken through the API should say so in HTTP terms."""

    @pytest.mark.parametrize("code,status", [
        ("slot_taken", 409),
        ("in_the_past", 400),
        ("bad_datetime", 400),
        ("treatment_not_found", 404),
        ("no_appointment", 404),
    ])
    def test_each_code_maps_to_a_status(self, code, status):
        from app.api.v1.appointments import fail
        with pytest.raises(HTTPException) as raised:
            fail({"error": code})
        assert raised.value.status_code == status

    def test_an_unrecognised_code_is_a_server_error(self):
        from app.api.v1.appointments import fail
        with pytest.raises(HTTPException) as raised:
            fail({"error": "something_new"})
        assert raised.value.status_code == 500

    def test_every_message_is_readable(self):
        from app.api.v1.appointments import ERROR_DETAIL, ERROR_STATUS
        assert set(ERROR_STATUS) == set(ERROR_DETAIL)
        for message in ERROR_DETAIL.values():
            assert message and message[0].isupper()


class TestDateRange:
    def test_a_range_is_parsed(self):
        from app.api.v1.appointments import bounds
        start, end = bounds("2027-06-01", "2027-06-30")
        assert start.day == 1 and end.day == 30

    def test_an_unparseable_date_is_rejected(self):
        from app.api.v1.appointments import bounds
        with pytest.raises(HTTPException) as raised:
            bounds("not-a-date", None)
        assert raised.value.status_code == 400

    def test_a_backwards_range_is_rejected(self):
        from app.api.v1.appointments import bounds
        with pytest.raises(HTTPException) as raised:
            bounds("2027-06-30", "2027-06-01")
        assert raised.value.status_code == 400

    def test_no_range_is_allowed(self):
        from app.api.v1.appointments import bounds
        assert bounds(None, None) == (None, None)


class TestEveryManagementRouterIsProtected:
    def test_no_router_forgets_authentication(self):
        """
        Auth is declared on the router, not per endpoint, so a new endpoint
        cannot be added without it. This checks that stays true.
        """
        from app.api.v1 import (appointments, business, customers, platform,
                                therapists, treatments)

        def names(module):
            return [getattr(d.dependency, "__name__", "") for d in module.router.dependencies]

        # Clinic data: a valid token, and the request held to one clinic.
        for module in (appointments, business, customers, therapists, treatments):
            assert "clinic_scope" in names(module), f"{module.__name__} is not held to a clinic"
        # The owner's pages: the owner and nobody else.
        assert "require_owner" in names(platform)

    def test_both_checks_start_from_a_valid_token(self):
        import inspect
        from app.dependencies import clinic_scope, current_user, require_owner
        for check in (clinic_scope, require_owner):
            defaults = [p.default for p in inspect.signature(check).parameters.values()]
            assert any(getattr(d, "dependency", None) is current_user for d in defaults), check.__name__


class TestPhoneNormalization:
    """
    The bot knows customers by the number WhatsApp sends. A number typed in
    the dashboard has to reach the same form, or the same person becomes
    two customers and their reminders go to a number WhatsApp cannot reach.
    """

    @pytest.mark.parametrize("typed", [
        "054-338-1998", "0543381998", "+972 54-338-1998", "972543381998",
        "00972543381998", "+972-54-338-1998", " 054 338 1998 ",
    ])
    def test_every_spelling_of_one_number_is_one_customer(self, typed):
        from app.core.schemas.api import normalize_phone
        assert normalize_phone(typed) == "972543381998"

    def test_a_foreign_number_keeps_its_country_code(self):
        from app.core.schemas.api import normalize_phone
        assert normalize_phone("+1 (555) 139-0508") == "15551390508"

    @pytest.mark.parametrize("bad", ["", None, "12", "abc", "1" * 16])
    def test_impossible_numbers_are_rejected(self, bad):
        from app.core.schemas.api import normalize_phone
        assert normalize_phone(bad) is None

    def test_booking_normalizes_the_number(self):
        from app.core.schemas.api import AppointmentIn
        body = AppointmentIn(customer_phone="054-338-1998", treatment_name="x",
                             date="2027-06-25", time="10:00")
        assert body.customer_phone == "972543381998"

    def test_booking_rejects_a_bad_number(self):
        from pydantic import ValidationError
        from app.core.schemas.api import AppointmentIn
        with pytest.raises(ValidationError):
            AppointmentIn(customer_phone="12345", treatment_name="x",
                          date="2027-06-25", time="10:00")


class TestBusinessDetails:
    def test_whatsapp_credentials_are_never_serialized(self):
        """The business row stores the WhatsApp token; the API must not."""
        from types import SimpleNamespace
        from app.api.v1.business import serialize
        row = SimpleNamespace(
            id="550e8400-e29b-41d4-a716-446655440000", name="Clinic",
            phone="0501234567", email=None, address=None,
            working_hours_start="09:00", working_hours_end="20:00",
            whatsapp_token="EAAG-secret", whatsapp_phone_id="123")
        out = serialize(row)
        assert "EAAG-secret" not in str(out)
        assert not any(key.startswith("whatsapp") for key in out)

    def test_the_schema_has_no_credential_fields(self):
        from app.core.schemas.api import BusinessOut
        assert not any("whatsapp" in field for field in BusinessOut.model_fields)

    def test_hours_are_validated(self):
        from pydantic import ValidationError
        from app.core.schemas.api import BusinessUpdate
        assert BusinessUpdate(working_hours_start="9:00").working_hours_start == "09:00"
        with pytest.raises(ValidationError):
            BusinessUpdate(working_hours_end="25:00")


class TestLoginRateLimit:
    @pytest.fixture(autouse=True)
    def fresh(self):
        from app.api.v1 import auth
        auth.reset()
        yield
        auth.reset()

    def test_a_few_mistakes_are_allowed(self):
        from app.api.v1 import auth
        for _ in range(auth.MAX_FAILURES - 1):
            auth.record_failure("1.2.3.4")
        assert auth.seconds_blocked("1.2.3.4") == 0

    def test_too_many_mistakes_block_that_address(self):
        from app.api.v1 import auth
        for _ in range(auth.MAX_FAILURES):
            auth.record_failure("1.2.3.4")
        assert auth.seconds_blocked("1.2.3.4") > 0

    def test_other_addresses_are_unaffected(self):
        from app.api.v1 import auth
        for _ in range(auth.MAX_FAILURES):
            auth.record_failure("1.2.3.4")
        assert auth.seconds_blocked("5.6.7.8") == 0

    def test_the_block_expires(self, monkeypatch):
        from app.api.v1 import auth
        clock = [1000.0]
        monkeypatch.setattr(auth.time, "monotonic", lambda: clock[0])
        for _ in range(auth.MAX_FAILURES):
            auth.record_failure("1.2.3.4")
        assert auth.seconds_blocked("1.2.3.4") > 0
        clock[0] += auth.WINDOW_SECONDS + 1
        assert auth.seconds_blocked("1.2.3.4") == 0

    def test_memory_is_bounded(self):
        from app.api.v1 import auth
        for i in range(auth.MAX_TRACKED + 50):
            auth.record_failure(f"10.0.{i // 256}.{i % 256}")
        assert len(auth._failures) <= auth.MAX_TRACKED


class TestLoginEndpoint:
    """Through the real app, with no database needed."""

    @pytest.fixture
    def client(self, monkeypatch):
        from fastapi.testclient import TestClient
        from app.api.v1 import auth
        from app.main import app
        monkeypatch.setattr(settings, "JWT_SECRET", "test-secret")
        monkeypatch.setattr(settings, "ADMIN_PASSWORD", "correct-horse")
        auth.reset()
        yield TestClient(app)
        auth.reset()

    def test_right_password_gets_a_token(self, client):
        response = client.post("/api/v1/auth/login", json={"password": "correct-horse"})
        assert response.status_code == 200
        assert response.json()["access_token"]

    def test_wrong_password_is_refused(self, client):
        response = client.post("/api/v1/auth/login", json={"password": "nope"})
        assert response.status_code == 401

    def test_repeated_guessing_is_stopped(self, client):
        from app.api.v1 import auth
        for _ in range(auth.MAX_FAILURES):
            client.post("/api/v1/auth/login", json={"password": "nope"})
        response = client.post("/api/v1/auth/login", json={"password": "correct-horse"})
        assert response.status_code == 429
        assert int(response.headers["Retry-After"]) > 0

    def test_a_success_clears_earlier_mistakes(self, client):
        from app.api.v1 import auth
        for _ in range(auth.MAX_FAILURES - 1):
            client.post("/api/v1/auth/login", json={"password": "nope"})
        client.post("/api/v1/auth/login", json={"password": "correct-horse"})
        for _ in range(auth.MAX_FAILURES - 1):
            client.post("/api/v1/auth/login", json={"password": "nope"})
        assert client.post("/api/v1/auth/login",
                           json={"password": "correct-horse"}).status_code == 200


class TestDashboardServing:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app)

    def test_the_root_leads_to_the_dashboard(self, client):
        response = client.get("/", follow_redirects=False)
        assert response.status_code in (302, 307)
        assert response.headers["location"] == "/dashboard/"

    def test_the_dashboard_page_is_served(self, client):
        response = client.get("/dashboard/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_scripts_are_limited_to_this_server(self, client):
        csp = client.get("/dashboard/").headers["content-security-policy"]
        assert "script-src 'self'" in csp
        assert "unsafe-inline" not in csp

    def test_the_page_cannot_be_framed(self, client):
        csp = client.get("/dashboard/").headers["content-security-policy"]
        assert "frame-ancestors 'none'" in csp

    def test_assets_are_revalidated(self, client):
        assert client.get("/dashboard/").headers["cache-control"] == "no-cache"

    def test_api_responses_do_not_get_dashboard_headers(self, client):
        response = client.get("/health")
        assert "content-security-policy" not in response.headers


class TestAuthStatus:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app)

    def test_reports_configured(self, client, monkeypatch):
        monkeypatch.setattr(settings, "JWT_SECRET", "s")
        monkeypatch.setattr(settings, "ADMIN_PASSWORD", "p")
        assert client.get("/api/v1/auth/status").json() == {"configured": True}

    @pytest.mark.parametrize("secret,password", [("", "p"), ("s", ""), ("", "")])
    def test_reports_missing_configuration(self, client, monkeypatch, secret, password):
        monkeypatch.setattr(settings, "JWT_SECRET", secret)
        monkeypatch.setattr(settings, "ADMIN_PASSWORD", password)
        assert client.get("/api/v1/auth/status").json() == {"configured": False}

    def test_reveals_nothing_else(self, client, monkeypatch):
        monkeypatch.setattr(settings, "ADMIN_PASSWORD", "hunter2")
        body = client.get("/api/v1/auth/status").text
        assert "hunter2" not in body and list(client.get("/api/v1/auth/status").json()) == ["configured"]


class TestWhichClinicARequestSees:
    """
    The rule that keeps clinics apart: a clinic login sees its own clinic
    and nothing else; the owner chooses.
    """

    @pytest.fixture
    def known(self, monkeypatch):
        from app.core import tenancy
        from app.services import accounts

        async def business_exists(business_id):
            return business_id in ("home", "clinic-b")

        problems = {}

        async def login_problem(user_id, business_id):
            return problems.get(user_id)

        monkeypatch.setattr(settings, "BUSINESS_ID", "home")
        monkeypatch.setattr(accounts, "business_exists", business_exists)
        monkeypatch.setattr(accounts, "login_problem", login_problem)
        tenancy._current.set(None)
        return problems

    async def scope(self, who, header=None):
        from contextlib import asynccontextmanager
        from app.core import tenancy
        from app.dependencies import clinic_scope
        before = tenancy.current_business_id()
        async with asynccontextmanager(clinic_scope)(principal=who, x_business_id=header) as chosen:
            assert tenancy.current_business_id() == chosen
        # Let go afterwards: the next request must not start in this clinic.
        assert tenancy.current_business_id() == before
        return chosen

    async def test_the_owner_gets_the_home_clinic_by_default(self, known):
        from app.dependencies import Principal
        assert await self.scope(Principal("owner")) == "home"

    async def test_the_owner_can_choose_any_clinic(self, known):
        from app.dependencies import Principal
        assert await self.scope(Principal("owner"), "clinic-b") == "clinic-b"

    async def test_the_owner_cannot_choose_a_clinic_that_does_not_exist(self, known):
        from app.dependencies import Principal
        with pytest.raises(HTTPException) as raised:
            await self.scope(Principal("owner"), "nowhere")
        assert raised.value.status_code == 404

    async def test_a_clinic_login_gets_its_own_clinic(self, known):
        from app.dependencies import Principal
        who = Principal("clinic", user_id="u-1", business_id="clinic-b")
        assert await self.scope(who) == "clinic-b"
        assert await self.scope(who, "clinic-b") == "clinic-b"

    async def test_a_clinic_login_cannot_ask_for_another_clinic(self, known):
        from app.dependencies import Principal
        who = Principal("clinic", user_id="u-1", business_id="clinic-b")
        with pytest.raises(HTTPException) as raised:
            await self.scope(who, "home")
        assert raised.value.status_code == 403

    async def test_a_login_switched_off_is_refused_at_once(self, known):
        from app.dependencies import Principal
        known["u-1"] = "This login has been turned off"
        with pytest.raises(HTTPException) as raised:
            await self.scope(Principal("clinic", user_id="u-1", business_id="clinic-b"))
        assert raised.value.status_code == 401

    async def test_a_login_with_the_owners_password_sees_nothing_yet(self, known):
        """Refused, but not signed out: it is asked for a password of its own."""
        from app.dependencies import Principal
        from app.services import accounts
        known["u-1"] = accounts.CHOOSE_PASSWORD
        with pytest.raises(HTTPException) as raised:
            await self.scope(Principal("clinic", user_id="u-1", business_id="clinic-b"))
        assert (raised.value.status_code, raised.value.detail) == (403, accounts.CHOOSE_PASSWORD)


class TestStartPassword:
    """The password the owner hands a clinic user, and replacing it."""

    def test_the_start_password_from_env_is_given(self, monkeypatch):
        from app.services import accounts
        monkeypatch.setattr(settings, "CLINIC_START_PASSWORD", "Start-2026")
        assert accounts.given_password() == "Start-2026"

    def test_without_one_each_user_gets_a_random_password(self, monkeypatch):
        from app.services import accounts
        monkeypatch.setattr(settings, "CLINIC_START_PASSWORD", "")
        first, second = accounts.given_password(), accounts.given_password()
        assert first != second and len(first) >= 12

    @pytest.mark.parametrize("new, reason", [
        ("Start-2026", "start password"),
        ("my-current-one", "same as the current"),
        ("short", "at least 8"),
    ])
    async def test_a_new_password_must_really_be_new(self, monkeypatch, new, reason):
        from app.services import accounts
        monkeypatch.setattr(settings, "CLINIC_START_PASSWORD", "Start-2026")
        with pytest.raises(accounts.AccountError) as refused:
            await accounts.change_password("u-1", "my-current-one", new)
        assert reason in str(refused.value)

    async def test_choosing_a_password_is_open_while_everything_else_is_closed(self, monkeypatch):
        """Who am I, and change my password, answer a login holding the owner's password."""
        from types import SimpleNamespace
        from app.services import accounts

        user = SimpleNamespace(business_id="clinic-b", is_active=True, must_change_password=True)
        business = SimpleNamespace(is_active=True)

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, query):
                return SimpleNamespace(first=lambda: (user, business))

        monkeypatch.setattr(accounts, "async_session", Session)
        user_id = "00000000-0000-0000-0000-000000000001"
        assert await accounts.login_problem(user_id, "clinic-b") == accounts.CHOOSE_PASSWORD
        assert await accounts.login_problem(user_id, "clinic-b", choosing_password=True) is None
        user.must_change_password = False
        assert await accounts.login_problem(user_id, "clinic-b") is None

    def test_the_dashboard_is_told_to_ask_for_a_new_password(self, configured, monkeypatch):
        from types import SimpleNamespace
        from fastapi.testclient import TestClient
        from app.main import app
        from app.services import accounts

        async def login_problem(user_id, business_id, choosing_password=False):
            return None if choosing_password else accounts.CHOOSE_PASSWORD

        async def get_login(user_id):
            return SimpleNamespace(email="dana@clinic.co.il", name=None, business_id="clinic-b",
                                   must_change_password=True)

        monkeypatch.setattr(accounts, "login_problem", login_problem)
        monkeypatch.setattr(accounts, "get_login", get_login)
        token = issue_token(subject="user:u-1", role="clinic", business_id="clinic-b")["access_token"]
        client = TestClient(app)
        me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200 and me.json()["must_change_password"] is True
        data = client.get("/api/v1/therapists", headers={"Authorization": f"Bearer {token}"})
        assert data.status_code == 403


class TestOwnerOnly:
    def test_a_clinic_login_cannot_reach_the_owners_pages(self):
        from app.dependencies import Principal, require_owner
        with pytest.raises(HTTPException) as raised:
            require_owner(Principal("clinic", user_id="u-1", business_id="clinic-b"))
        assert raised.value.status_code == 403

    def test_the_owner_can(self):
        from app.dependencies import Principal, require_owner
        assert require_owner(Principal("owner")).is_owner

    def test_the_route_refuses_a_clinic_token(self, configured):
        from fastapi.testclient import TestClient
        from app.main import app
        token = issue_token(subject="user:u-1", role="clinic", business_id="clinic-b")["access_token"]
        response = TestClient(app).get("/api/v1/platform/clinics",
                                       headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 403


class TestClinicCard:
    """What the owner's Clinics page is told about each clinic."""

    HOME = "11111111-1111-1111-1111-111111111111"
    OTHER = "22222222-2222-2222-2222-222222222222"

    @pytest.fixture(autouse=True)
    def env_number(self, monkeypatch):
        monkeypatch.setattr(settings, "BUSINESS_ID", self.HOME)
        monkeypatch.setattr(settings, "WHATSAPP_PHONE_ID", "1072796699244200")

    def card(self, clinic_id, phone_id=None, token=None):
        import uuid
        from types import SimpleNamespace
        from app.api.v1.platform import serialize_clinic
        business = SimpleNamespace(
            id=uuid.UUID(clinic_id), name="Clinic", phone="03-555-1234", email=None, address=None,
            working_hours_start="09:00", working_hours_end="19:00",
            whatsapp_phone_id=phone_id, whatsapp_token=token, is_active=True)
        return serialize_clinic(business, logins={business.id: 2}, upcoming={})

    def test_the_main_clinic_without_its_own_number_sends_from_the_env_one(self):
        card = self.card(self.HOME)
        assert card["is_home"] and card["whatsapp_from_env"]

    def test_the_main_clinic_with_its_own_number_uses_that(self):
        assert not self.card(self.HOME, phone_id="555000111")["whatsapp_from_env"]

    def test_another_clinic_never_borrows_the_env_number(self):
        card = self.card(self.OTHER)
        assert not card["is_home"] and not card["whatsapp_from_env"]

    def test_a_clinics_token_is_never_sent_back(self):
        card = self.card(self.OTHER, phone_id="555000111", token="EAAG-secret")
        assert card["has_own_token"] is True
        assert "EAAG-secret" not in str(card)

    def test_counts_are_per_clinic(self):
        card = self.card(self.HOME)
        assert (card["logins"], card["upcoming_appointments"]) == (2, 0)


class TestClinicSignIn:
    @pytest.fixture
    def client(self, configured, monkeypatch):
        from types import SimpleNamespace
        from fastapi.testclient import TestClient
        from app.api.v1 import auth
        from app.main import app
        from app.services import accounts

        auth.reset()

        async def authenticate(email, password):
            if email == "dana@clinic.co.il" and password == "right-password":
                return SimpleNamespace(id="u-1", business_id="clinic-b")
            return None

        monkeypatch.setattr(accounts, "authenticate", authenticate)
        yield TestClient(app)
        auth.reset()

    def test_a_clinic_signs_in_with_email_and_gets_its_clinic(self, client):
        response = client.post("/api/v1/auth/login",
                               json={"email": "dana@clinic.co.il", "password": "right-password"})
        assert response.status_code == 200
        who = current_user(response.json()["access_token"])
        assert (who.role, who.user_id, who.business_id) == ("clinic", "u-1", "clinic-b")

    def test_a_wrong_password_is_refused_without_saying_which_part(self, client):
        response = client.post("/api/v1/auth/login",
                               json={"email": "dana@clinic.co.il", "password": "wrong"})
        assert response.status_code == 401
        assert response.json()["detail"] == "Incorrect email or password"

    def test_wrong_clinic_passwords_count_towards_the_limit(self, client):
        from app.api.v1 import auth
        for _ in range(auth.MAX_FAILURES):
            client.post("/api/v1/auth/login", json={"email": "x@y.co", "password": "wrong"})
        response = client.post("/api/v1/auth/login",
                               json={"email": "dana@clinic.co.il", "password": "right-password"})
        assert response.status_code == 429

    def test_without_an_email_it_is_the_owners_password(self, client):
        response = client.post("/api/v1/auth/login", json={"password": "correct-horse"})
        assert response.status_code == 200
        assert current_user(response.json()["access_token"]).is_owner

    def test_the_owner_is_told_who_they_are(self, client):
        token = client.post("/api/v1/auth/login", json={"password": "correct-horse"}).json()["access_token"]
        response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert response.json()["role"] == "owner"

    def test_the_owner_password_cannot_be_changed_through_the_api(self, client):
        token = client.post("/api/v1/auth/login", json={"password": "correct-horse"}).json()["access_token"]
        response = client.post("/api/v1/auth/password",
                               json={"current_password": "correct-horse", "new_password": "something-new"},
                               headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 400
