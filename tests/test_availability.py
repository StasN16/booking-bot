from datetime import date, datetime, time
from types import SimpleNamespace

import pytest

from app.services.availability import compute_available_slots, parse_hhmm


def therapist(name="נועה", days="Monday,Tuesday", start="09:00", end="12:00", id_="t1"):
    return SimpleNamespace(
        id=id_,
        name=name,
        working_days=days,
        working_hours_start=start,
        working_hours_end=end,
    )


def appointment(therapist_id, start, end):
    return SimpleNamespace(therapist_id=therapist_id, start_time=start, end_time=end)


# 2025-06-02 is a Monday, comfortably in the past so "now" never interferes.
MONDAY = date(2025, 6, 2)
TUESDAY = date(2025, 6, 3)
WEDNESDAY = date(2025, 6, 4)


class TestParseHhmm:
    def test_parses_hours_and_minutes(self):
        assert parse_hhmm("09:30") == time(9, 30)

    def test_parses_bare_hour(self):
        assert parse_hhmm("09") == time(9, 0)

    def test_rejects_malformed(self):
        assert parse_hhmm("") is None
        assert parse_hhmm("abc") is None
        assert parse_hhmm("99:00") is None


class TestWorkingDays:
    def test_no_slots_when_therapist_is_off(self):
        """A therapist who doesn't work Wednesday offers nothing on Wednesday."""
        slots = compute_available_slots([therapist()], [], WEDNESDAY, 60)
        assert slots == []

    def test_slots_on_a_working_day(self):
        slots = compute_available_slots([therapist()], [], MONDAY, 60)
        assert [s["time"] for s in slots] == ["09:00", "09:30", "10:00", "10:30", "11:00"]

    def test_empty_therapist_list(self):
        assert compute_available_slots([], [], MONDAY, 60) == []


class TestDuration:
    def test_last_slot_must_fit_before_closing(self):
        """A 60 minute treatment cannot start at 11:30 in a 09:00-12:00 day."""
        slots = compute_available_slots([therapist()], [], MONDAY, 60)
        assert "11:30" not in [s["time"] for s in slots]

    def test_shorter_treatment_fits_later(self):
        slots = compute_available_slots([therapist()], [], MONDAY, 30)
        assert "11:30" in [s["time"] for s in slots]

    def test_treatment_longer_than_the_day_yields_nothing(self):
        assert compute_available_slots([therapist()], [], MONDAY, 600) == []


class TestConflicts:
    def test_booked_slot_is_removed(self):
        booked = appointment("t1", datetime(2025, 6, 2, 9, 0), datetime(2025, 6, 2, 10, 0))
        times = [s["time"] for s in compute_available_slots([therapist()], [booked], MONDAY, 60)]
        assert "09:00" not in times
        assert "10:00" in times

    def test_partial_overlap_blocks_the_slot(self):
        """A 09:30-10:30 booking must also block a 60 minute 09:00 start."""
        booked = appointment("t1", datetime(2025, 6, 2, 9, 30), datetime(2025, 6, 2, 10, 30))
        times = [s["time"] for s in compute_available_slots([therapist()], [booked], MONDAY, 60)]
        assert "09:00" not in times
        assert "09:30" not in times
        assert "10:30" in times

    def test_adjacent_booking_does_not_block(self):
        """Back-to-back is fine - 09:00-10:00 booked still allows a 10:00 start."""
        booked = appointment("t1", datetime(2025, 6, 2, 9, 0), datetime(2025, 6, 2, 10, 0))
        times = [s["time"] for s in compute_available_slots([therapist()], [booked], MONDAY, 60)]
        assert "10:00" in times

    def test_another_therapists_booking_is_ignored(self):
        booked = appointment("SOMEONE_ELSE", datetime(2025, 6, 2, 9, 0), datetime(2025, 6, 2, 10, 0))
        times = [s["time"] for s in compute_available_slots([therapist()], [booked], MONDAY, 60)]
        assert "09:00" in times

    def test_fully_booked_day_returns_nothing(self):
        booked = appointment("t1", datetime(2025, 6, 2, 9, 0), datetime(2025, 6, 2, 12, 0))
        assert compute_available_slots([therapist()], [booked], MONDAY, 60) == []


class TestMultipleTherapists:
    def test_each_therapist_contributes_slots(self):
        a = therapist(name="נועה", id_="t1", days="Monday", start="09:00", end="11:00")
        b = therapist(name="יעל", id_="t2", days="Monday", start="10:00", end="12:00")
        slots = compute_available_slots([a, b], [], MONDAY, 60)
        names = {s["therapist_name"] for s in slots}
        assert names == {"נועה", "יעל"}

    def test_only_therapists_working_that_day_appear(self):
        a = therapist(name="נועה", id_="t1", days="Monday")
        b = therapist(name="יעל", id_="t2", days="Tuesday")
        slots = compute_available_slots([a, b], [], MONDAY, 60)
        assert {s["therapist_name"] for s in slots} == {"נועה"}


class TestPastSlots:
    def test_past_slots_are_skipped_for_today(self):
        now = datetime(2025, 6, 2, 10, 15)
        times = [
            s["time"]
            for s in compute_available_slots([therapist()], [], MONDAY, 30, now=now)
        ]
        assert "09:00" not in times
        assert "10:00" not in times
        assert times[0] == "10:30"  # rounded up to the next step

    def test_future_date_is_unaffected_by_now(self):
        now = datetime(2025, 6, 2, 10, 15)
        a = therapist(days="Monday,Tuesday")
        times = [
            s["time"]
            for s in compute_available_slots([a], [], TUESDAY, 60, now=now)
        ]
        assert times[0] == "09:00"


class TestMalformedData:
    def test_therapist_with_broken_hours_is_skipped_not_crashed(self):
        broken = therapist(name="broken", start="not-a-time", end="12:00")
        good = therapist(name="good", id_="t2")
        slots = compute_available_slots([broken, good], [], MONDAY, 60)
        assert {s["therapist_name"] for s in slots} == {"good"}

    def test_therapist_with_no_working_days_is_skipped(self):
        assert compute_available_slots([therapist(days=None)], [], MONDAY, 60) == []
