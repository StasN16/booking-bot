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
        assert current_user(token) == "owner"

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
        from app.api.v1 import (appointments, business, customers,
                                therapists, treatments)

        for module in (appointments, business, customers, therapists, treatments):
            names = [
                getattr(d.dependency, "__name__", "")
                for d in module.router.dependencies
            ]
            assert "current_user" in names, f"{module.__name__} is unprotected"


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
