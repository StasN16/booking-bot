"""
Timezone handling for appointment times.

Appointment columns are DateTime(timezone=True), so PostgreSQL hands back
aware datetimes, while anything built with datetime.combine() is naive.
Comparing the two raises TypeError, so every datetime crossing between the
database and the scheduling logic goes through here first.

A naive value is read as clinic-local time, which is what the bot means
whenever it says "10:00".
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import settings

CLINIC_TZ = ZoneInfo(settings.TIMEZONE)


def now() -> datetime:
    """Current time, as an aware datetime in the clinic's timezone."""
    return datetime.now(CLINIC_TZ)


def to_clinic_tz(value: datetime | None) -> datetime | None:
    """Return `value` as an aware datetime in the clinic's timezone."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=CLINIC_TZ)
    return value.astimezone(CLINIC_TZ)
