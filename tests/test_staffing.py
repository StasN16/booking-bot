"""
Who does which treatment, and who gets booked when nobody is asked for.

A therapist with nothing ticked does every treatment, so a team that never
set this up keeps working exactly as before.
"""
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.core.timeutils import to_clinic_tz
from app.services.availability import works_then
from app.services.booking import choose_therapist
from app.services.staffing import can_do

MASSAGE, FACIAL = uuid.uuid4(), uuid.uuid4()
# 2025-06-02 is a Monday.
MONDAY_10 = to_clinic_tz(datetime(2025, 6, 2, 10, 0))


def therapist(name, days="Monday", start="09:00", end="17:00"):
    return SimpleNamespace(id=uuid.uuid4(), name=name, working_days=days,
                           working_hours_start=start, working_hours_end=end)


class TestCanDo:
    def test_nothing_ticked_means_every_treatment(self):
        assert can_do(None, MASSAGE) and can_do(set(), FACIAL)

    def test_ticked_means_only_those(self):
        assert can_do({MASSAGE}, MASSAGE)
        assert not can_do({MASSAGE}, FACIAL)

    def test_an_id_given_as_text_counts_too(self):
        assert can_do({MASSAGE}, str(MASSAGE))


class TestWorksThen:
    def test_inside_the_hours_on_a_working_day(self):
        assert works_then(therapist("Noa"), MONDAY_10, MONDAY_10 + timedelta(hours=1))

    def test_not_on_a_day_off(self):
        assert not works_then(therapist("Noa", days="Tuesday"), MONDAY_10, MONDAY_10 + timedelta(hours=1))

    def test_not_running_past_closing(self):
        late = therapist("Noa", end="10:30")
        assert not works_then(late, MONDAY_10, MONDAY_10 + timedelta(hours=1))

    def test_exactly_until_closing_is_fine(self):
        assert works_then(therapist("Noa", end="11:00"), MONDAY_10, MONDAY_10 + timedelta(hours=1))

    def test_broken_hours_are_never_working(self):
        assert not works_then(therapist("Noa", start="soon"), MONDAY_10, MONDAY_10 + timedelta(hours=1))


class TestChoosingWhenNobodyIsAskedFor:
    def test_someone_free_who_works_then(self):
        off_today = therapist("Off", days="Tuesday")
        on_today = therapist("On")
        end = MONDAY_10 + timedelta(hours=1)
        assert choose_therapist([off_today, on_today], set(), MONDAY_10, end) is on_today

    def test_never_someone_already_booked(self):
        busy, free = therapist("Busy"), therapist("Free")
        end = MONDAY_10 + timedelta(hours=1)
        assert choose_therapist([busy, free], {busy.id}, MONDAY_10, end) is free

    def test_out_of_hours_it_still_finds_someone_free(self):
        """As before: a time nobody works can still be booked with someone free."""
        only = therapist("Only", days="Tuesday")
        end = MONDAY_10 + timedelta(hours=1)
        assert choose_therapist([only], set(), MONDAY_10, end) is only

    def test_everyone_booked_means_nobody(self):
        busy = therapist("Busy")
        assert choose_therapist([busy], {busy.id}, MONDAY_10, MONDAY_10 + timedelta(hours=1)) is None
