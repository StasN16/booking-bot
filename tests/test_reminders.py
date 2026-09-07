from datetime import datetime, timedelta, timezone

import pytest

from app.core.timeutils import CLINIC_TZ
from app.services.reminders import (
    MESSAGES,
    REMINDER_KINDS,
    due_window,
    is_due,
    reminder_text,
)


NOW = datetime(2027, 6, 25, 12, 0, tzinfo=CLINIC_TZ)


def at(**delta):
    """An appointment start time relative to NOW."""
    return NOW + timedelta(**delta)


class TestDueWindow:
    def test_24h_window_ends_a_day_out(self):
        earliest, latest = due_window("24h", NOW)
        assert latest == NOW + timedelta(hours=24)
        assert earliest == NOW + timedelta(hours=22)

    def test_1h_window_ends_an_hour_out(self):
        earliest, latest = due_window("1h", NOW)
        assert latest == NOW + timedelta(hours=1)
        assert earliest == NOW + timedelta(minutes=15)

    def test_windows_do_not_overlap(self):
        """An appointment must never match both reminders at one moment."""
        early_24, late_24 = due_window("24h", NOW)
        early_1, late_1 = due_window("1h", NOW)
        assert late_1 < early_24


class TestIs24hDue:
    @pytest.mark.parametrize("hours", [22, 23, 24])
    def test_due_inside_the_window(self, hours):
        assert is_due(at(hours=hours), "24h", NOW) is True

    @pytest.mark.parametrize("hours", [21, 25, 48])
    def test_not_due_outside_the_window(self, hours):
        assert is_due(at(hours=hours), "24h", NOW) is False

    def test_an_appointment_an_hour_away_is_not_a_tomorrow_reminder(self):
        """It would otherwise tell someone "tomorrow" about a 13:00 today."""
        assert is_due(at(hours=1), "24h", NOW) is False

    def test_past_appointment_is_not_due(self):
        assert is_due(at(hours=-2), "24h", NOW) is False


class TestIs1hDue:
    @pytest.mark.parametrize("minutes", [15, 30, 45, 60])
    def test_due_inside_the_window(self, minutes):
        assert is_due(at(minutes=minutes), "1h", NOW) is True

    @pytest.mark.parametrize("minutes", [10, 75, 120])
    def test_not_due_outside_the_window(self, minutes):
        assert is_due(at(minutes=minutes), "1h", NOW) is False

    def test_past_appointment_is_not_due(self):
        assert is_due(at(minutes=-5), "1h", NOW) is False


class TestPollingCoverage:
    def test_every_appointment_is_caught_by_some_poll(self):
        """
        With the configured interval, no appointment can slip between polls.
        Walk a poll schedule and confirm a fixed appointment becomes due.
        """
        from app.config import settings
        interval = timedelta(minutes=settings.REMINDER_POLL_MINUTES)
        appointment = NOW + timedelta(hours=30)

        for kind in REMINDER_KINDS:
            hits = 0
            poll = NOW
            while poll < appointment:
                if is_due(appointment, kind, poll):
                    hits += 1
                poll += interval
            assert hits > 0, f"{kind} reminder would never fire"

    def test_window_is_wider_than_the_poll_interval(self):
        """A window narrower than the interval could be stepped over."""
        from app.config import settings
        interval = timedelta(minutes=settings.REMINDER_POLL_MINUTES)
        for kind, spec in REMINDER_KINDS.items():
            assert spec["window"] > interval, f"{kind} window is too narrow"


class TestNaiveDatetimes:
    def test_naive_start_time_is_handled(self):
        """Start times can arrive naive; comparing them raw would raise."""
        naive = (NOW + timedelta(hours=23)).replace(tzinfo=None)
        assert is_due(naive, "24h", NOW) is True

    def test_utc_start_time_is_converted(self):
        utc = (NOW + timedelta(hours=23)).astimezone(timezone.utc)
        assert is_due(utc, "24h", NOW) is True


class TestReminderText:
    @pytest.mark.parametrize("kind", ["24h", "1h"])
    @pytest.mark.parametrize("language", ["he", "en", "ru"])
    def test_every_kind_and_language_renders(self, kind, language):
        text = reminder_text(kind, language, "עיסוי שוודי", "נועה", "14:00")
        assert text
        assert "{" not in text  # no unfilled placeholders

    def test_24h_mentions_treatment_and_therapist(self):
        text = reminder_text("24h", "en", "Facial", "Yael", "14:00")
        assert "Facial" in text and "Yael" in text and "14:00" in text

    def test_unknown_language_falls_back(self):
        assert reminder_text("1h", "fr", "x", "y", "14:00") == \
               reminder_text("1h", "he", "x", "y", "14:00")

    @pytest.mark.parametrize("kind", ["24h", "1h"])
    def test_all_languages_present_for_each_kind(self, kind):
        assert set(MESSAGES[kind]) == {"he", "en", "ru"}

    def test_no_emoji_or_exclamation(self):
        """Maya's voice: no emoji, no exclamation marks."""
        for kind in MESSAGES:
            for text in MESSAGES[kind].values():
                assert "!" not in text
                assert not any(ord(c) > 0x2600 for c in text)


class TestConfiguration:
    def test_each_kind_names_a_real_column(self):
        from app.core.models.appointment import Appointment
        for spec in REMINDER_KINDS.values():
            assert hasattr(Appointment, spec["flag"])

    def test_flags_are_distinct(self):
        flags = [s["flag"] for s in REMINDER_KINDS.values()]
        assert len(flags) == len(set(flags))
