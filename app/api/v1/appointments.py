"""
Managing appointments from the dashboard.

Creating, rescheduling and cancelling go through app/services/booking so
the dashboard obeys the same rules as the bot: no double booking, no
appointments in the past, the same treatment matching. A second
implementation here would be a second set of rules to keep in step.
"""
import datetime as dt
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.config import settings
from app.core.db import async_session
from app.core.models.appointment import Appointment
from app.core.models.customer import Customer
from app.core.models.therapist import Therapist
from app.core.models.treatment import Treatment
from app.core.schemas.api import (
    AppointmentIn,
    AppointmentOut,
    AppointmentReschedule,
    StatsOut,
)
from app.core.timeutils import now as clinic_now, to_clinic_tz
from app.dependencies import current_user
from app.services.booking import (
    cancel_appointment,
    create_appointment,
    reschedule_appointment,
)
from app.services.date_parser import parse_date

logger = logging.getLogger(__name__)
router = APIRouter(tags=["appointments"], dependencies=[Depends(current_user)])

BUSINESS_ID = settings.BUSINESS_ID

# Booking services answer with codes; the API answers with status numbers.
ERROR_STATUS = {
    "slot_taken": 409,
    "in_the_past": 400,
    "bad_datetime": 400,
    "treatment_not_found": 404,
    "no_therapist_available": 409,
    "customer_not_found": 404,
    "no_appointment": 404,
}

ERROR_DETAIL = {
    "slot_taken": "That therapist is already booked then",
    "in_the_past": "That time has already passed",
    "bad_datetime": "Could not read the date or time",
    "treatment_not_found": "No such treatment",
    "no_therapist_available": "No active therapist to assign",
    "customer_not_found": "No such customer",
    "no_appointment": "No matching appointment",
}


def fail(result: dict):
    """Turn a booking service error into an HTTP response."""
    code = result.get("error", "")
    raise HTTPException(
        status_code=ERROR_STATUS.get(code, 500),
        detail=ERROR_DETAIL.get(code, "Could not complete the request"),
    )


async def serialize(session, appointment: Appointment) -> dict:
    treatment = await session.get(Treatment, appointment.treatment_id)
    therapist = await session.get(Therapist, appointment.therapist_id)
    customer = await session.get(Customer, appointment.customer_id)

    start = to_clinic_tz(appointment.start_time)
    end = to_clinic_tz(appointment.end_time)

    return {
        "id": str(appointment.id),
        "status": appointment.status,
        "date": start.strftime("%Y-%m-%d"),
        "time": start.strftime("%H:%M"),
        "end_time": end.strftime("%H:%M"),
        "treatment": treatment.name if treatment else None,
        "therapist": therapist.name if therapist else None,
        "customer_name": customer.name if customer else None,
        "customer_phone": customer.phone if customer else None,
        "price": treatment.price if treatment else None,
        "reminder_24h_sent": bool(appointment.reminder_24h_sent),
        "reminder_1h_sent": bool(appointment.reminder_1h_sent),
    }


def bounds(from_date: str | None, to_date: str | None):
    """Resolve a date range, defaulting to everything from today onward."""
    start = parse_date(from_date) if from_date else None
    end = parse_date(to_date) if to_date else None
    if from_date and start is None:
        raise HTTPException(status_code=400, detail=f"Bad from_date: {from_date}")
    if to_date and end is None:
        raise HTTPException(status_code=400, detail=f"Bad to_date: {to_date}")
    if start and end and end < start:
        raise HTTPException(status_code=400, detail="to_date is before from_date")
    return start, end


@router.get("/appointments", response_model=list[AppointmentOut])
async def list_appointments(
    from_date: str | None = Query(default=None, examples=["2027-06-01"]),
    to_date: str | None = Query(default=None, examples=["2027-06-30"]),
    status: str | None = Query(default=None, examples=["confirmed"]),
    therapist_id: str | None = None,
    customer_phone: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
):
    """List appointments, newest first within the range."""
    start, end = bounds(from_date, to_date)

    async with async_session() as session:
        query = select(Appointment).where(Appointment.business_id == BUSINESS_ID)

        if start:
            query = query.where(Appointment.start_time >= to_clinic_tz(
                dt.datetime.combine(start, dt.time.min)))
        if end:
            query = query.where(Appointment.start_time <= to_clinic_tz(
                dt.datetime.combine(end, dt.time.max)))
        if status:
            query = query.where(Appointment.status == status)
        if therapist_id:
            try:
                query = query.where(Appointment.therapist_id == uuid.UUID(therapist_id))
            except ValueError:
                raise HTTPException(status_code=400, detail="Bad therapist_id")
        if customer_phone:
            found = await session.execute(
                select(Customer).where(
                    Customer.phone == customer_phone,
                    Customer.business_id == BUSINESS_ID,
                )
            )
            customer = found.scalar_one_or_none()
            if not customer:
                return []
            query = query.where(Appointment.customer_id == customer.id)

        result = await session.execute(
            query.order_by(Appointment.start_time).limit(limit)
        )
        return [await serialize(session, a) for a in result.scalars().all()]


@router.post("/appointments", response_model=AppointmentOut, status_code=201)
async def book_appointment(body: AppointmentIn):
    """Book on a customer's behalf, under the same rules as the bot."""
    result = await create_appointment(
        customer_phone=body.customer_phone,
        treatment_name=body.treatment_name,
        therapist_name=body.therapist_name or "",
        appointment_date=body.date,
        appointment_time=body.time,
    )
    if not result.get("success"):
        fail(result)

    async with async_session() as session:
        appointment = await session.get(
            Appointment, uuid.UUID(result["appointment_id"]))
        return await serialize(session, appointment)


@router.get("/appointments/{appointment_id}", response_model=AppointmentOut)
async def get_appointment(appointment_id: str):
    async with async_session() as session:
        try:
            appointment = await session.get(Appointment, uuid.UUID(appointment_id))
        except ValueError:
            raise HTTPException(status_code=404, detail="No such appointment")
        if not appointment or str(appointment.business_id) != BUSINESS_ID:
            raise HTTPException(status_code=404, detail="No such appointment")
        return await serialize(session, appointment)


@router.put("/appointments/{appointment_id}", response_model=AppointmentOut)
async def move_appointment(appointment_id: str, body: AppointmentReschedule):
    """Move an appointment, refusing a slot that is already taken."""
    async with async_session() as session:
        try:
            appointment = await session.get(Appointment, uuid.UUID(appointment_id))
        except ValueError:
            raise HTTPException(status_code=404, detail="No such appointment")
        if not appointment or str(appointment.business_id) != BUSINESS_ID:
            raise HTTPException(status_code=404, detail="No such appointment")
        customer = await session.get(Customer, appointment.customer_id)
        phone = customer.phone if customer else None

    if not phone:
        raise HTTPException(status_code=409, detail="Appointment has no customer")

    result = await reschedule_appointment(
        customer_phone=phone,
        new_date=body.date,
        new_time=body.time,
        appointment_id=appointment_id,
    )
    if not result.get("success"):
        fail(result)

    async with async_session() as session:
        appointment = await session.get(Appointment, uuid.UUID(appointment_id))
        return await serialize(session, appointment)


@router.delete("/appointments/{appointment_id}")
async def cancel(appointment_id: str):
    """Cancel an appointment, keeping the row so history stays readable."""
    async with async_session() as session:
        try:
            appointment = await session.get(Appointment, uuid.UUID(appointment_id))
        except ValueError:
            raise HTTPException(status_code=404, detail="No such appointment")
        if not appointment or str(appointment.business_id) != BUSINESS_ID:
            raise HTTPException(status_code=404, detail="No such appointment")
        customer = await session.get(Customer, appointment.customer_id)
        phone = customer.phone if customer else None

    if not phone:
        raise HTTPException(status_code=409, detail="Appointment has no customer")

    result = await cancel_appointment(phone, appointment_id=appointment_id)
    if not result.get("success"):
        fail(result)
    return {"status": "cancelled", "id": appointment_id, **result}


@router.get("/stats", response_model=StatsOut)
async def statistics(
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
):
    """
    Counts and revenue over a date range.

    Revenue counts confirmed appointments only, so a cancelled booking does
    not appear as money taken.
    """
    start, end = bounds(from_date, to_date)
    start = start or (clinic_now().date() - dt.timedelta(days=30))
    end = end or (clinic_now().date() + dt.timedelta(days=30))

    async with async_session() as session:
        result = await session.execute(
            select(Appointment).where(
                Appointment.business_id == BUSINESS_ID,
                Appointment.start_time >= to_clinic_tz(
                    dt.datetime.combine(start, dt.time.min)),
                Appointment.start_time <= to_clinic_tz(
                    dt.datetime.combine(end, dt.time.max)),
            )
        )
        appointments = result.scalars().all()

        confirmed = [a for a in appointments if a.status == "confirmed"]
        cancelled = [a for a in appointments if a.status == "cancelled"]

        by_treatment: dict[str, int] = {}
        by_therapist: dict[str, int] = {}
        revenue = 0

        for appointment in confirmed:
            treatment = await session.get(Treatment, appointment.treatment_id)
            therapist = await session.get(Therapist, appointment.therapist_id)
            if treatment:
                by_treatment[treatment.name] = by_treatment.get(treatment.name, 0) + 1
                revenue += treatment.price or 0
            if therapist:
                by_therapist[therapist.name] = by_therapist.get(therapist.name, 0) + 1

        return {
            "from_date": start.isoformat(),
            "to_date": end.isoformat(),
            "appointments": len(confirmed),
            "cancelled": len(cancelled),
            "revenue": revenue,
            "by_treatment": dict(sorted(by_treatment.items(),
                                        key=lambda kv: -kv[1])),
            "by_therapist": dict(sorted(by_therapist.items(),
                                        key=lambda kv: -kv[1])),
        }
