import logging
import uuid
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from app.config import settings
from app.core.models.customer import Customer
from app.core.models.appointment import Appointment
from app.core.models.treatment import Treatment
from app.core.models.therapist import Therapist
from app.core.timeutils import now as clinic_now, to_clinic_tz
from app.services.date_parser import parse_date, parse_time

logger = logging.getLogger(__name__)

engine = create_async_engine(settings.DATABASE_URL)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

BUSINESS_ID = settings.BUSINESS_ID


def pick_best_match(candidates: list, query: str):
    """
    Choose the record whose name best matches what the customer said.

    A plain LIKE '%...%' is ambiguous here - "עיסוי" matches both
    "עיסוי שוודי" and "עיסוי רקמות עמוק" - so prefer the most specific
    match and never let ambiguity raise.
    """
    if not candidates or not query:
        return None

    q = query.strip().lower()
    if not q:
        return None

    def name_of(c):
        return (c.name or "").strip().lower()

    for predicate in (
        lambda n: n == q,
        lambda n: n.startswith(q),
        lambda n: q in n,
        lambda n: n and n in q,
    ):
        for candidate in candidates:
            if predicate(name_of(candidate)):
                return candidate

    return None


def build_start_time(appointment_date: str, appointment_time: str) -> datetime | None:
    """Combine a date string and a time string into a start datetime."""
    parsed_date = parse_date(appointment_date)
    if parsed_date is None:
        try:
            parsed_date = datetime.strptime(appointment_date, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    normalized_time = parse_time(appointment_time)
    if normalized_time is None:
        return None

    hour, minute = normalized_time.split(":")
    naive = datetime.combine(parsed_date, datetime.min.time()).replace(
        hour=int(hour), minute=int(minute)
    )
    return to_clinic_tz(naive)


async def _find_conflict(session, therapist_id, start_time, end_time, exclude_id=None):
    """Return an overlapping confirmed appointment for this therapist, if any."""
    query = select(Appointment).where(
        Appointment.therapist_id == therapist_id,
        Appointment.status == "confirmed",
        Appointment.start_time < end_time,
        Appointment.end_time > start_time,
    )
    if exclude_id is not None:
        query = query.where(Appointment.id != exclude_id)
    result = await session.execute(query)
    return result.scalars().first()


async def _get_customer(session, customer_phone: str):
    result = await session.execute(
        select(Customer).where(
            Customer.phone == customer_phone,
            Customer.business_id == BUSINESS_ID
        )
    )
    return result.scalar_one_or_none()


async def _next_appointment(session, customer):
    """The customer's soonest upcoming confirmed appointment."""
    result = await session.execute(
        select(Appointment).where(
            Appointment.customer_id == customer.id,
            Appointment.status == "confirmed",
            Appointment.start_time > clinic_now()
        ).order_by(Appointment.start_time)
    )
    return result.scalars().first()


async def get_or_create_customer(phone: str, name: str = None) -> Customer:
    """Get existing customer or create new one"""
    async with async_session() as session:
        customer = await _get_customer(session, phone)

        if not customer:
            customer = Customer(
                id=uuid.uuid4(),
                business_id=BUSINESS_ID,
                phone=phone,
                name=name,
                conversation_state="idle"
            )
            session.add(customer)
            await session.commit()
            await session.refresh(customer)
            logger.info(f"Created new customer: {phone}")

        return customer


async def create_appointment(
    customer_phone: str,
    treatment_name: str,
    therapist_name: str,
    appointment_date: str,
    appointment_time: str
) -> dict:
    """Create a new appointment"""
    async with async_session() as session:
        try:
            customer = await _get_customer(session, customer_phone)

            if not customer:
                customer = Customer(
                    id=uuid.uuid4(),
                    business_id=BUSINESS_ID,
                    phone=customer_phone,
                    conversation_state="idle"
                )
                session.add(customer)
                await session.flush()

            result = await session.execute(
                select(Treatment).where(
                    Treatment.business_id == BUSINESS_ID,
                    Treatment.is_active == True
                )
            )
            treatment = pick_best_match(list(result.scalars().all()), treatment_name)

            if not treatment:
                return {"success": False, "error": "treatment_not_found"}

            result = await session.execute(
                select(Therapist).where(
                    Therapist.business_id == BUSINESS_ID,
                    Therapist.is_active == True
                )
            )
            therapists = list(result.scalars().all())
            therapist = pick_best_match(therapists, therapist_name)

            if not therapist:
                # No specific therapist asked for - any active one will do.
                therapist = therapists[0] if therapists else None

            if not therapist:
                return {"success": False, "error": "no_therapist_available"}

            start_time = build_start_time(appointment_date, appointment_time)
            if start_time is None:
                logger.warning(
                    f"Could not parse date/time: {appointment_date!r} {appointment_time!r}"
                )
                return {"success": False, "error": "bad_datetime"}

            end_time = start_time + timedelta(minutes=treatment.duration_minutes)

            if start_time < clinic_now():
                return {"success": False, "error": "in_the_past"}

            if await _find_conflict(session, therapist.id, start_time, end_time):
                return {"success": False, "error": "slot_taken"}

            appointment = Appointment(
                id=uuid.uuid4(),
                business_id=BUSINESS_ID,
                customer_id=customer.id,
                therapist_id=therapist.id,
                treatment_id=treatment.id,
                start_time=start_time,
                end_time=end_time,
                status="confirmed",
                reminder_sent=False
            )
            session.add(appointment)
            await session.commit()

            logger.info(
                f"Created appointment for {customer_phone}: "
                f"{treatment.name} on {start_time:%Y-%m-%d %H:%M}"
            )

            return {
                "success": True,
                "appointment_id": str(appointment.id),
                "treatment": treatment.name,
                "therapist": therapist.name,
                "date": start_time.strftime("%Y-%m-%d"),
                "time": start_time.strftime("%H:%M"),
                "duration": treatment.duration_minutes,
                "price": treatment.price
            }

        except Exception as e:
            logger.error(f"Error creating appointment: {e}")
            await session.rollback()
            return {"success": False, "error": "internal_error"}


async def reschedule_appointment(
    customer_phone: str,
    new_date: str,
    new_time: str,
    appointment_id: str = None
) -> dict:
    """Move an existing appointment to a new date and time."""
    async with async_session() as session:
        try:
            customer = await _get_customer(session, customer_phone)
            if not customer:
                return {"success": False, "error": "customer_not_found"}

            if appointment_id:
                appointment = await session.get(Appointment, appointment_id)
                if appointment and appointment.customer_id != customer.id:
                    appointment = None
            else:
                appointment = await _next_appointment(session, customer)

            if not appointment:
                return {"success": False, "error": "no_appointment"}

            treatment = await session.get(Treatment, appointment.treatment_id)
            if not treatment:
                return {"success": False, "error": "treatment_not_found"}

            start_time = build_start_time(new_date, new_time)
            if start_time is None:
                return {"success": False, "error": "bad_datetime"}

            if start_time < clinic_now():
                return {"success": False, "error": "in_the_past"}

            end_time = start_time + timedelta(minutes=treatment.duration_minutes)

            conflict = await _find_conflict(
                session,
                appointment.therapist_id,
                start_time,
                end_time,
                exclude_id=appointment.id,
            )
            if conflict:
                return {"success": False, "error": "slot_taken"}

            old_start = appointment.start_time
            appointment.start_time = start_time
            appointment.end_time = end_time
            appointment.reminder_sent = False
            await session.commit()

            therapist = await session.get(Therapist, appointment.therapist_id)

            logger.info(
                f"Rescheduled appointment {appointment.id} for {customer_phone}: "
                f"{old_start:%Y-%m-%d %H:%M} -> {start_time:%Y-%m-%d %H:%M}"
            )

            return {
                "success": True,
                "appointment_id": str(appointment.id),
                "treatment": treatment.name,
                "therapist": therapist.name if therapist else "",
                "date": start_time.strftime("%Y-%m-%d"),
                "time": start_time.strftime("%H:%M"),
            }

        except Exception as e:
            logger.error(f"Error rescheduling appointment: {e}")
            await session.rollback()
            return {"success": False, "error": "internal_error"}


async def cancel_appointment(customer_phone: str, appointment_id: str = None) -> dict:
    """Cancel an appointment"""
    async with async_session() as session:
        try:
            customer = await _get_customer(session, customer_phone)
            if not customer:
                return {"success": False, "error": "customer_not_found"}

            if appointment_id:
                appointment = await session.get(Appointment, appointment_id)
                if appointment and appointment.customer_id != customer.id:
                    appointment = None
            else:
                appointment = await _next_appointment(session, customer)

            if not appointment:
                return {"success": False, "error": "no_appointment"}

            treatment = await session.get(Treatment, appointment.treatment_id)

            appointment.status = "cancelled"
            await session.commit()

            logger.info(f"Cancelled appointment {appointment.id} for {customer_phone}")

            local_start = to_clinic_tz(appointment.start_time)

            return {
                "success": True,
                "treatment": treatment.name if treatment else "",
                "date": local_start.strftime("%Y-%m-%d"),
                "time": local_start.strftime("%H:%M"),
            }

        except Exception as e:
            logger.error(f"Error cancelling appointment: {e}")
            await session.rollback()
            return {"success": False, "error": "internal_error"}


async def get_customer_appointments(customer_phone: str) -> list:
    """Get upcoming appointments for a customer"""
    async with async_session() as session:
        customer = await _get_customer(session, customer_phone)

        if not customer:
            return []

        result = await session.execute(
            select(Appointment).where(
                Appointment.customer_id == customer.id,
                Appointment.status == "confirmed",
                Appointment.start_time > clinic_now()
            ).order_by(Appointment.start_time)
        )
        appointments = result.scalars().all()

        result_list = []
        for app in appointments:
            treatment = await session.get(Treatment, app.treatment_id)
            therapist = await session.get(Therapist, app.therapist_id)
            local_start = to_clinic_tz(app.start_time)
            result_list.append({
                "id": str(app.id),
                "treatment": treatment.name if treatment else "Unknown",
                "therapist": therapist.name if therapist else "Unknown",
                "date": local_start.strftime("%Y-%m-%d"),
                "time": local_start.strftime("%H:%M"),
                "status": app.status
            })

        return result_list
