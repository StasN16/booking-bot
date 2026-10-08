"""
Request and response shapes for the management API.

Kept separate from the ORM models so what the dashboard sends and receives
can be validated and changed without touching the database, and so a
column added later is not exposed by accident.
"""
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator


# --- phone numbers ---------------------------------------------------------

def normalize_phone(value: str | None) -> str | None:
    """
    Write a phone number the way WhatsApp sends it: digits only, country
    code first, no leading + or 00.

    The bot learns customers from WhatsApp, so "054-338-1998" typed in the
    dashboard has to become "972543381998" or it would create a second
    customer, and that customer's reminders would go to a number WhatsApp
    cannot reach. A number with a single leading 0 is taken as Israeli, as
    this clinic's customers are; anything else must include its country code.
    """
    if not value:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]                      # 00 international prefix
    elif digits.startswith("0") and len(digits) in (9, 10):
        digits = "972" + digits[1:]              # Israeli local number
    if not 8 <= len(digits) <= 15:               # E.164 allows at most 15
        return None
    return digits


# --- auth ------------------------------------------------------------------

class LoginRequest(BaseModel):
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_at: str


# --- treatments ------------------------------------------------------------

class TreatmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    duration_minutes: int = Field(gt=0, le=600)
    price: int = Field(ge=0)
    description: str | None = Field(default=None, max_length=1000)
    is_active: bool = True


class TreatmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    duration_minutes: int | None = Field(default=None, gt=0, le=600)
    price: int | None = Field(default=None, ge=0)
    description: str | None = Field(default=None, max_length=1000)
    is_active: bool | None = None


class TreatmentOut(BaseModel):
    id: str
    name: str
    duration_minutes: int
    price: int
    description: str | None = None
    is_active: bool


# --- therapists ------------------------------------------------------------

VALID_DAYS = {"Sunday", "Monday", "Tuesday", "Wednesday",
              "Thursday", "Friday", "Saturday"}


def validate_days(value: str | None) -> str | None:
    """
    Working days are stored as a comma-separated string of English day names.

    Availability matches on those exact names, so a typo here would quietly
    remove a therapist from every search rather than fail loudly.
    """
    if value is None:
        return None
    days = [d.strip().capitalize() for d in value.split(",") if d.strip()]
    unknown = [d for d in days if d not in VALID_DAYS]
    if unknown:
        raise ValueError(
            f"Unknown day(s): {', '.join(unknown)}. "
            f"Use English names: {', '.join(sorted(VALID_DAYS))}"
        )
    return ",".join(days)


def validate_hhmm(value: str | None) -> str | None:
    """Working hours must parse as HH:MM, for the same reason."""
    if value is None:
        return None
    parts = value.strip().split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        raise ValueError(f"Expected HH:MM, got {value!r}")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Not a real time: {value!r}")
    return f"{hour:02d}:{minute:02d}"


class TherapistIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=255)
    working_days: str = Field(examples=["Sunday,Monday,Tuesday"])
    working_hours_start: str = Field(examples=["09:00"])
    working_hours_end: str = Field(examples=["18:00"])
    is_active: bool = True

    _days = field_validator("working_days")(validate_days)
    _start = field_validator("working_hours_start")(validate_hhmm)
    _end = field_validator("working_hours_end")(validate_hhmm)


class TherapistUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=255)
    working_days: str | None = None
    working_hours_start: str | None = None
    working_hours_end: str | None = None
    is_active: bool | None = None

    _days = field_validator("working_days")(validate_days)
    _start = field_validator("working_hours_start")(validate_hhmm)
    _end = field_validator("working_hours_end")(validate_hhmm)


class TherapistOut(BaseModel):
    id: str
    name: str
    phone: str | None = None
    email: str | None = None
    working_days: str | None = None
    working_hours_start: str | None = None
    working_hours_end: str | None = None
    is_active: bool


# --- appointments ----------------------------------------------------------

class AppointmentIn(BaseModel):
    customer_phone: str = Field(min_length=5, max_length=20)
    # A name typed by the clinic, for customers who booked by phone call.
    customer_name: str | None = Field(default=None, max_length=255)
    treatment_name: str
    therapist_name: str | None = None
    date: str = Field(examples=["2027-06-25"])
    time: str = Field(examples=["14:00"])
    notes: str | None = Field(default=None, max_length=1000)

    @field_validator("customer_phone")
    @classmethod
    def as_whatsapp_number(cls, value: str) -> str:
        normalized = normalize_phone(value)
        if normalized is None:
            raise ValueError("Expected a phone number, e.g. 054-338-1998 "
                             "or 972543381998")
        return normalized


class AppointmentReschedule(BaseModel):
    date: str
    time: str


class AppointmentNotes(BaseModel):
    notes: str | None = Field(default=None, max_length=1000)


class AppointmentOut(BaseModel):
    id: str
    status: str
    date: str
    time: str
    end_time: str
    duration_minutes: int | None = None
    treatment: str | None = None
    treatment_id: str | None = None
    therapist: str | None = None
    therapist_id: str | None = None
    customer_id: str | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    price: int | None = None
    notes: str | None = None
    reminder_24h_sent: bool = False
    reminder_1h_sent: bool = False


# --- availability ----------------------------------------------------------

class SlotOut(BaseModel):
    time: str
    therapist_id: str
    therapist_name: str


class AvailabilityOut(BaseModel):
    date: str
    treatment_id: str
    treatment: str
    duration_minutes: int
    slots: list[SlotOut]


# --- business --------------------------------------------------------------

class BusinessOut(BaseModel):
    # The WhatsApp credentials stored on this row are deliberately absent.
    id: str
    name: str
    phone: str
    email: str | None = None
    address: str | None = None
    working_hours_start: str | None = None
    working_hours_end: str | None = None
    timezone: str


class BusinessUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    phone: str | None = Field(default=None, min_length=5, max_length=20)
    email: str | None = Field(default=None, max_length=255)
    address: str | None = Field(default=None, max_length=500)
    working_hours_start: str | None = None
    working_hours_end: str | None = None

    _start = field_validator("working_hours_start")(validate_hhmm)
    _end = field_validator("working_hours_end")(validate_hhmm)


# --- customers -------------------------------------------------------------

class CustomerOut(BaseModel):
    id: str
    name: str | None = None
    phone: str
    language: str | None = None
    is_blocked: bool = False
    conversation_state: str | None = None
    appointments: int = 0


class CustomerUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    is_blocked: bool | None = None


# --- statistics ------------------------------------------------------------

class DayStats(BaseModel):
    date: str
    appointments: int
    revenue: int


class StatsOut(BaseModel):
    from_date: str
    to_date: str
    appointments: int
    cancelled: int
    revenue: int
    cancellation_rate: float
    by_treatment: dict[str, int]
    by_therapist: dict[str, int]
    by_day: list[DayStats]
