from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.timeutils import CLINIC_TZ, now as clinic_now, to_clinic_tz
from app.services.booking import build_start_time, pick_best_match
from app.services.conversation import booking_failure, t, day_name
from app.core.enums import ConversationState


class TestClinicTimezone:
    def test_now_is_aware(self):
        assert clinic_now().tzinfo is not None

    def test_naive_is_read_as_clinic_local(self):
        """"10:00" from the bot means 10:00 in the clinic, not 10:00 UTC."""
        result = to_clinic_tz(datetime(2027, 6, 25, 10, 0))
        assert result.hour == 10
        assert result.tzinfo is not None

    def test_aware_utc_is_converted_not_relabelled(self):
        """A UTC value out of the database must shift to clinic wall-clock."""
        utc = datetime(2027, 6, 25, 7, 0, tzinfo=timezone.utc)
        result = to_clinic_tz(utc)
        assert result.utcoffset() == timedelta(hours=3)  # Israel summer time
        assert result.hour == 10

    def test_none_passes_through(self):
        assert to_clinic_tz(None) is None

    def test_naive_and_aware_become_comparable(self):
        """The crash this fixes: comparing the two kinds raised TypeError."""
        naive = to_clinic_tz(datetime(2027, 6, 25, 10, 0))
        aware = to_clinic_tz(datetime(2027, 6, 25, 7, 0, tzinfo=timezone.utc))
        assert naive == aware


def named(name):
    return SimpleNamespace(name=name)


TREATMENTS = [
    named("עיסוי שוודי"),
    named("טיפול פנים"),
    named("רפלקסולוגיה"),
    named("עיסוי רקמות עמוק"),
]


class TestPickBestMatch:
    def test_exact_match_wins(self):
        assert pick_best_match(TREATMENTS, "עיסוי שוודי").name == "עיסוי שוודי"

    def test_ambiguous_prefix_does_not_crash(self):
        """The original bug: 'עיסוי' matched two rows and raised MultipleResultsFound."""
        match = pick_best_match(TREATMENTS, "עיסוי")
        assert match is not None
        assert match.name.startswith("עיסוי")

    def test_prefix_beats_substring(self):
        """'עיסוי' should prefer the treatment that starts with it."""
        assert pick_best_match(TREATMENTS, "עיסוי").name == "עיסוי שוודי"

    def test_substring_match(self):
        assert pick_best_match(TREATMENTS, "פנים").name == "טיפול פנים"

    def test_case_insensitive(self):
        items = [named("Swedish Massage")]
        assert pick_best_match(items, "swedish massage") is not None
        assert pick_best_match(items, "SWEDISH") is not None

    def test_name_contained_in_a_longer_sentence(self):
        """Customers say more than the bare name."""
        match = pick_best_match(TREATMENTS, "אני רוצה רפלקסולוגיה בבקשה")
        assert match.name == "רפלקסולוגיה"

    def test_no_match_returns_none(self):
        assert pick_best_match(TREATMENTS, "פדיקור") is None

    @pytest.mark.parametrize("query", ["", None, "   "])
    def test_empty_query_returns_none(self, query):
        assert pick_best_match(TREATMENTS, query) is None

    def test_empty_candidates_returns_none(self):
        assert pick_best_match([], "עיסוי") is None


def clinic_dt(*args):
    """Expected value: an aware datetime in the clinic's timezone."""
    return datetime(*args, tzinfo=CLINIC_TZ)


class TestBuildStartTime:
    def test_iso_date_and_time(self):
        assert build_start_time("2027-06-25", "14:00") == clinic_dt(2027, 6, 25, 14, 0)

    def test_result_is_timezone_aware(self):
        """Appointment columns are tz-aware; a naive value raises on compare."""
        assert build_start_time("2027-06-25", "14:00").tzinfo is not None

    def test_accepts_natural_language_date(self):
        result = build_start_time("מחר", "14:00")
        assert result.date() == date.today() + timedelta(days=1)
        assert result.hour == 14

    def test_accepts_pm_time(self):
        """The original bug: strptime('%H:%M') crashed on '2pm'."""
        assert build_start_time("2027-06-25", "2pm") == clinic_dt(2027, 6, 25, 14, 0)

    def test_accepts_dotted_time(self):
        assert build_start_time("2027-06-25", "14.30") == clinic_dt(2027, 6, 25, 14, 30)

    def test_accepts_time_range(self):
        assert build_start_time("2027-06-25", "14:00-15:00") == clinic_dt(2027, 6, 25, 14, 0)

    @pytest.mark.parametrize("bad_time", ["", None, "sometime"])
    def test_unparseable_time_returns_none(self, bad_time):
        assert build_start_time("2027-06-25", bad_time) is None

    @pytest.mark.parametrize("bad_date", ["", None, "gibberish"])
    def test_unparseable_date_returns_none(self, bad_date):
        assert build_start_time(bad_date, "14:00") is None


class TestLocalizedCopy:
    @pytest.mark.parametrize("language", ["he", "en", "ru"])
    def test_every_language_has_every_key(self, language):
        from app.services.conversation import MESSAGES
        expected = set(MESSAGES["he"])
        assert set(MESSAGES[language]) == expected

    def test_reply_uses_the_customers_language(self):
        assert t("en", "no_appointments") == "You don't have any upcoming appointments"
        assert t("he", "no_appointments") == "אין לך תורים קרובים"

    def test_unknown_language_falls_back_to_default(self):
        assert t("fr", "no_appointments") == t("he", "no_appointments")

    def test_placeholders_are_filled(self):
        reply = t("en", "booked", treatment="Facial", therapist="Yael",
                  date="2027-06-25", time="14:00")
        assert "Facial" in reply and "Yael" in reply and "14:00" in reply

    def test_missing_placeholder_does_not_raise(self):
        assert t("en", "booked") != ""

    def test_day_names_are_localized(self):
        monday = date(2025, 6, 2)
        assert day_name(monday, "he") == "יום שני"
        assert day_name(monday, "en") == "Monday"
        assert day_name(monday, "ru") == "понедельник"


class TestBookingFailure:
    def test_slot_taken_returns_to_time_selection(self):
        reply, state = booking_failure("en", {"error": "slot_taken"}, ConversationState.CHOOSING_TIME)
        assert "taken" in reply.lower()
        assert state == ConversationState.CHOOSING_TIME

    def test_unknown_treatment_returns_to_treatment_choice(self):
        _, state = booking_failure("en", {"error": "treatment_not_found"}, ConversationState.CHOOSING_TIME)
        assert state == ConversationState.CHOOSING_TREATMENT

    def test_missing_appointment_goes_idle(self):
        _, state = booking_failure("en", {"error": "no_appointment"}, ConversationState.RESCHEDULING)
        assert state == ConversationState.IDLE

    def test_unrecognised_error_is_handled(self):
        reply, state = booking_failure("en", {"error": "something_new"}, ConversationState.IDLE)
        assert reply
        assert state == ConversationState.IDLE
