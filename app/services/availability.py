import logging
from datetime import datetime, date, time, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from app.config import settings
from app.core.timeutils import now as clinic_now, to_clinic_tz
from app.core.models.business import Business
from app.core.models.therapist import Therapist
from app.core.models.treatment import Treatment
from app.core.models.appointment import Appointment

logger = logging.getLogger(__name__)

engine = create_async_engine(settings.DATABASE_URL)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

BUSINESS_ID = settings.BUSINESS_ID

SLOT_STEP_MINUTES = 30

async def get_treatments() -> list:
    """Get all active treatments"""
    async with async_session() as session:
        result = await session.execute(
            select(Treatment).where(
                Treatment.business_id == BUSINESS_ID,
                Treatment.is_active == True
            )
        )
        treatments = result.scalars().all()
        return [
            {
                "id": str(t.id),
                "name": t.name,
                "duration_minutes": t.duration_minutes,
                "price": t.price,
                "description": t.description
            }
            for t in treatments
        ]

async def get_therapists() -> list:
    """Get all active therapists"""
    async with async_session() as session:
        result = await session.execute(
            select(Therapist).where(
                Therapist.business_id == BUSINESS_ID,
                Therapist.is_active == True
            )
        )
        therapists = result.scalars().all()
        return [
            {
                "id": str(t.id),
                "name": t.name,
                "working_days": t.working_days,
                "working_hours_start": t.working_hours_start,
                "working_hours_end": t.working_hours_end
            }
            for t in therapists
        ]


def parse_hhmm(value: str) -> time | None:
    """Parse a stored 'HH:MM' working-hours string. Returns None if malformed."""
    if not value:
        return None
    parts = value.split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour=hour, minute=minute)


def compute_available_slots(
    therapists,
    appointments,
    target_date: date,
    duration_minutes: int,
    now: datetime = None,
    step_minutes: int = SLOT_STEP_MINUTES,
) -> list:
    """
    Work out the free slots for a treatment on a date.

    Kept free of any database access so the scheduling rules can be tested
    directly. `therapists` and `appointments` are any objects exposing the
    same attributes as the ORM models.
    """
    day_name = target_date.strftime("%A")
    working_today = [
        t for t in therapists
        if t.working_days and day_name in t.working_days
    ]
    if not working_today:
        return []

    available_slots = []

    for therapist in working_today:
        start = parse_hhmm(therapist.working_hours_start)
        end = parse_hhmm(therapist.working_hours_end)
        if start is None or end is None:
            logger.warning(f"Therapist {therapist.name} has malformed working hours, skipping")
            continue

        current_time = to_clinic_tz(datetime.combine(target_date, start))
        end_time = to_clinic_tz(datetime.combine(target_date, end))

        # A slot in the past can't be booked, so start from the next step.
        if now is not None and to_clinic_tz(now).date() == target_date:
            earliest = to_clinic_tz(now).replace(second=0, microsecond=0)
            if current_time < earliest:
                current_time = earliest
                remainder = current_time.minute % step_minutes
                if remainder:
                    current_time += timedelta(minutes=step_minutes - remainder)

        while current_time + timedelta(minutes=duration_minutes) <= end_time:
            slot_end = current_time + timedelta(minutes=duration_minutes)

            # Two intervals overlap when each starts before the other ends.
            # Appointment times come from the database as aware datetimes, so
            # normalize before comparing or the two kinds raise TypeError.
            is_taken = any(
                a.therapist_id == therapist.id
                and current_time < to_clinic_tz(a.end_time)
                and slot_end > to_clinic_tz(a.start_time)
                for a in appointments
            )

            if not is_taken:
                available_slots.append({
                    "time": current_time.strftime("%H:%M"),
                    "therapist_id": str(therapist.id),
                    "therapist_name": therapist.name
                })

            current_time += timedelta(minutes=step_minutes)

    return available_slots


async def get_available_slots(treatment_id: str, target_date: date) -> list:
    """Get available time slots for a treatment on a specific date"""
    async with async_session() as session:
        treatment = await session.get(Treatment, treatment_id)
        if not treatment:
            return []

        result = await session.execute(
            select(Therapist).where(
                Therapist.business_id == BUSINESS_ID,
                Therapist.is_active == True
            )
        )
        therapists = result.scalars().all()

        start_of_day = datetime.combine(target_date, datetime.min.time())
        end_of_day = datetime.combine(target_date, datetime.max.time())

        result = await session.execute(
            select(Appointment).where(
                Appointment.business_id == BUSINESS_ID,
                Appointment.start_time >= start_of_day,
                Appointment.start_time <= end_of_day,
                Appointment.status == "confirmed"
            )
        )
        existing_appointments = result.scalars().all()

        return compute_available_slots(
            therapists=therapists,
            appointments=existing_appointments,
            target_date=target_date,
            duration_minutes=treatment.duration_minutes,
            now=clinic_now(),
        )


async def get_treatments_summary() -> str:
    """Get a text summary of treatments for the bot"""
    treatments = await get_treatments()
    if not treatments:
        return "אין טיפולים זמינים כרגע"

    lines = []
    for t in treatments:
        lines.append(f"- {t['name']}: {t['duration_minutes']} דקות, {t['price']}₪")

    return "\n".join(lines)


async def get_therapists_summary() -> str:
    """Get a text summary of therapists for the bot"""
    therapists = await get_therapists()
    if not therapists:
        return ""

    day_names_he = {
        "Sunday": "ראשון", "Monday": "שני", "Tuesday": "שלישי",
        "Wednesday": "רביעי", "Thursday": "חמישי", "Friday": "שישי", "Saturday": "שבת"
    }

    lines = []
    for t in therapists:
        days_he = ", ".join(day_names_he.get(d.strip(), d) for d in t["working_days"].split(","))
        lines.append(f"- {t['name']}: {t['working_hours_start']}-{t['working_hours_end']}, ימים: {days_he}")

    return "\n".join(lines)


async def find_next_available_date(treatment_id: str, start_date: date, max_days: int = 7) -> date | None:
    """Search forward up to max_days to find a date with available slots."""
    for i in range(1, max_days + 1):
        check_date = start_date + timedelta(days=i)
        slots = await get_available_slots(treatment_id, check_date)
        if slots:
            return check_date
    return None
