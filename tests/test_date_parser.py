from datetime import date, timedelta

import pytest

from app.services.date_parser import parse_date, parse_time


TODAY = date.today()


class TestKeywords:
    def test_today_hebrew(self):
        assert parse_date("היום") == TODAY

    def test_today_english(self):
        assert parse_date("today") == TODAY

    def test_tomorrow_hebrew(self):
        assert parse_date("מחר") == TODAY + timedelta(days=1)

    def test_tomorrow_english(self):
        assert parse_date("tomorrow") == TODAY + timedelta(days=1)

    def test_tomorrow_survives_month_end(self):
        """The original bug: date(y, m, day + 1) blew up on the 31st."""
        for anchor in (date(2025, 1, 31), date(2025, 12, 31), date(2024, 2, 29)):
            assert anchor + timedelta(days=1) == date.fromordinal(anchor.toordinal() + 1)


class TestExplicitDates:
    def test_iso_format(self):
        assert parse_date("2027-06-25") == date(2027, 6, 25)

    def test_day_month_slash(self):
        parsed = parse_date("25/06")
        assert parsed.day == 25 and parsed.month == 6

    def test_day_month_dot(self):
        parsed = parse_date("25.06")
        assert parsed.day == 25 and parsed.month == 6

    def test_day_month_rolls_to_next_year_when_past(self):
        """A bare day/month that already passed should mean next year."""
        yesterday = TODAY - timedelta(days=1)
        parsed = parse_date(f"{yesterday.day:02d}/{yesterday.month:02d}")
        assert parsed >= TODAY

    def test_explicit_year_is_respected(self):
        assert parse_date("25/06/2027") == date(2027, 6, 25)

    def test_impossible_date_returns_none(self):
        assert parse_date("45/13") is None
        assert parse_date("2025-02-30") is None


class TestWeekdays:
    @pytest.mark.parametrize("text,weekday", [
        ("יום ראשון", 6),
        ("יום שני", 0),
        ("יום שלישי", 1),
        ("יום רביעי", 2),
        ("יום חמישי", 3),
        ("Sunday", 6),
        ("monday", 0),
        ("Thursday", 3),
    ])
    def test_weekday_names_resolve_to_that_weekday(self, text, weekday):
        parsed = parse_date(text)
        assert parsed is not None, f"failed to parse {text!r}"
        assert parsed.weekday() == weekday

    def test_weekday_is_always_in_the_future(self):
        for text in ("Sunday", "Monday", "יום שישי", "שבת"):
            assert parse_date(text) > TODAY


class TestUnparseable:
    @pytest.mark.parametrize("text", ["", None, "asdfgh", "בלה בלה"])
    def test_returns_none(self, text):
        assert parse_date(text) is None


class TestParseTime:
    @pytest.mark.parametrize("text,expected", [
        ("14:00", "14:00"),
        ("14.00", "14:00"),
        ("9:30", "09:30"),
        ("09:05", "09:05"),
        ("2pm", "14:00"),
        ("2 PM", "14:00"),
        ("11am", "11:00"),
        ("12am", "00:00"),
        ("12pm", "12:00"),
        ("14", "14:00"),
    ])
    def test_normalizes_to_hhmm(self, text, expected):
        assert parse_time(text) == expected

    def test_range_takes_the_start(self):
        assert parse_time("14:00-15:00") == "14:00"
        assert parse_time("14:00 to 15:00") == "14:00"

    @pytest.mark.parametrize("text", ["", None, "בוקר", "whenever"])
    def test_unparseable_returns_none(self, text):
        assert parse_time(text) is None

    def test_rejects_out_of_range(self):
        assert parse_time("99:99") is None
