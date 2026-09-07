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
        from app.api.v1 import appointments, customers, therapists, treatments

        for module in (appointments, customers, therapists, treatments):
            names = [
                getattr(d.dependency, "__name__", "")
                for d in module.router.dependencies
            ]
            assert "current_user" in names, f"{module.__name__} is unprotected"
