"""
Appointment reminders.

send_due_reminders() is the whole job: find appointments coming up, message
the customer, record that it went out. It is deliberately a plain function
so it can be driven either by the background loop in app/main.py or by an
HTTP call from an external scheduler, without the logic caring which.
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, update

from app.config import settings
from app.core.db import async_session
from app.core.models.appointment import Appointment
from app.core.models.customer import Customer
from app.core.models.therapist import Therapist
from app.core.models.treatment import Treatment
from app.core.timeutils import now as clinic_now, to_clinic_tz
from app.services.whatsapp import send_message

logger = logging.getLogger(__name__)


BUSINESS_ID = settings.BUSINESS_ID

# lead:   how far ahead of the appointment this reminder is meant to land.
# window: how far back from that point a reminder still counts as due, so a
#         poll that runs every few minutes cannot step over one. Keep the
#         window narrower than the gap between reminders, or an appointment
#         an hour away would also match the "tomorrow" reminder.
REMINDER_KINDS = {
    "24h": {"lead": timedelta(hours=24), "window": timedelta(hours=2),
            "flag": "reminder_24h_sent"},
    "1h": {"lead": timedelta(hours=1), "window": timedelta(minutes=45),
           "flag": "reminder_1h_sent"},
}

DEFAULT_LANGUAGE = "he"

MESSAGES = {
    "24h": {
        "he": "היי, מחר יש לך תור ל{treatment} עם {therapist} בשעה {time}",
        "en": "Hey, you have {treatment} with {therapist} tomorrow at {time}",
        "ru": "Здравствуйте, завтра у вас {treatment} у {therapist} в {time}",
    },
    "1h": {
        "he": "תזכורת, התור שלך בעוד שעה, ב{time}",
        "en": "Reminder, your appointment is in an hour, at {time}",
        "ru": "Напоминание, ваша запись через час, в {time}",
    },
}


def due_window(kind: str, now: datetime) -> tuple[datetime, datetime]:
    """Start times that should receive this reminder when polled at `now`."""
    spec = REMINDER_KINDS[kind]
    latest = now + spec["lead"]
    return latest - spec["window"], latest


def is_due(start_time: datetime, kind: str, now: datetime) -> bool:
    """Whether an appointment starting at `start_time` needs this reminder."""
    earliest, latest = due_window(kind, now)
    start_time = to_clinic_tz(start_time)
    return earliest <= start_time <= latest


def reminder_text(kind: str, language: str, treatment: str, therapist: str, time: str) -> str:
    catalog = MESSAGES[kind]
    template = catalog.get(language) or catalog[DEFAULT_LANGUAGE]
    return template.format(treatment=treatment, therapist=therapist, time=time)


async def send_due_reminders(now: datetime = None) -> dict:
    """
    Send every reminder that is currently due.

    Returns counts per kind. Safe to call as often as you like: a reminder is
    claimed in the database before it is sent, so overlapping runs cannot
    message the same customer twice.
    """
    now = now or clinic_now()
    sent = {kind: 0 for kind in REMINDER_KINDS}
    failed = 0

    async with async_session() as session:
        for kind, spec in REMINDER_KINDS.items():
            earliest, latest = due_window(kind, now)
            flag = spec["flag"]

            result = await session.execute(
                select(Appointment).where(
                    Appointment.business_id == BUSINESS_ID,
                    Appointment.status == "confirmed",
                    getattr(Appointment, flag).is_(False),
                    Appointment.start_time >= earliest,
                    Appointment.start_time <= latest,
                )
            )
            due = result.scalars().all()

            for appointment in due:
                if not await claim(session, appointment.id, flag):
                    continue  # another run got there first

                if await deliver(session, appointment, kind):
                    sent[kind] += 1
                else:
                    failed += 1

    total = sum(sent.values())
    if total or failed:
        logger.info(f"Reminders sent: {sent}, failed: {failed}")
    return {"sent": sent, "failed": failed, "total": total}


async def claim(session, appointment_id, flag: str) -> bool:
    """
    Mark the reminder as sent, returning False if it already was.

    Claiming before sending means a crash costs one reminder rather than
    messaging a customer repeatedly.
    """
    result = await session.execute(
        update(Appointment)
        .where(Appointment.id == appointment_id, getattr(Appointment, flag).is_(False))
        .values(**{flag: True})
        .returning(Appointment.id)
    )
    claimed = result.scalar_one_or_none() is not None
    await session.commit()
    return claimed


async def deliver(session, appointment, kind: str) -> bool:
    """Build and send the message for one appointment."""
    try:
        customer = await session.get(Customer, appointment.customer_id)
        if not customer or not customer.phone:
            logger.warning(f"Appointment {appointment.id} has no reachable customer")
            return False
        if customer.is_blocked:
            return False

        treatment = await session.get(Treatment, appointment.treatment_id)
        therapist = await session.get(Therapist, appointment.therapist_id)
        local_start = to_clinic_tz(appointment.start_time)

        text = reminder_text(
            kind=kind,
            language=customer.language or DEFAULT_LANGUAGE,
            treatment=treatment.name if treatment else "",
            therapist=therapist.name if therapist else "",
            time=local_start.strftime("%H:%M"),
        )
        return bool(await send_message(customer.phone, text))

    except Exception as e:
        logger.exception(f"Failed to send {kind} reminder for {appointment.id}: {e}")
        return False
