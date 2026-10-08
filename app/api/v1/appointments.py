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
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.core.db import async_session
from app.core.tenancy import current_business_id
from app.core.models.appointment import Appointment
from app.core.models.customer import Customer
from app.core.models.therapist import Therapist
from app.core.models.treatment import Treatment
from app.core.schemas.api import (
    AppointmentIn,
    AppointmentNotes,
    AppointmentOut,
    AppointmentReschedule,
    AvailabilityOut,
    StatsOut,
    normalize_phone,
)
from app.core.timeutils import now as clinic_now, to_clinic_tz
from app.dependencies import clinic_scope
from app.services.booking import (
    cancel_appointment,
    create_appointment,
    reschedule_appointment,
)
from app.services.availability import get_available_slots
from app.services.date_parser import parse_date

logger = logging.getLogger(__name__)
router = APIRouter(tags=["appointments"], dependencies=[Depends(clinic_scope)])


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


async def serialize(session, appointment: Appointment, loaded: dict | None = None) -> dict:
    """
    The appointment as the dashboard sees it. `loaded` maps ids to
    treatments, therapists and customers already fetched, to skip
    looking each one up again.
    """
    loaded = loaded or {}
    treatment = (loaded.get(appointment.treatment_id)
                 or await session.get(Treatment, appointment.treatment_id))
    therapist = (loaded.get(appointment.therapist_id)
                 or await session.get(Therapist, appointment.therapist_id))
    customer = (loaded.get(appointment.customer_id)
                or await session.get(Customer, appointment.customer_id))

    start = to_clinic_tz(appointment.start_time)
    end = to_clinic_tz(appointment.end_time)

    return {
        "id": str(appointment.id),
        "status": appointment.status,
        "date": start.strftime("%Y-%m-%d"),
        "time": start.strftime("%H:%M"),
        "end_time": end.strftime("%H:%M"),
        "duration_minutes": int((end - start).total_seconds() // 60),
        "treatment": treatment.name if treatment else None,
        "treatment_id": str(appointment.treatment_id),
        "therapist": therapist.name if therapist else None,
        "therapist_id": str(appointment.therapist_id),
        "customer_id": str(appointment.customer_id),
        "customer_name": customer.name if customer else None,
        "customer_phone": customer.phone if customer else None,
        "price": treatment.price if treatment else None,
        "notes": appointment.notes,
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
    from_date: Annotated[str | None, Query(examples=["2027-06-01"])] = None,
    to_date: Annotated[str | None, Query(examples=["2027-06-30"])] = None,
    status: Annotated[str | None, Query(examples=["confirmed"])] = None,
    therapist_id: str | None = None,
    customer_phone: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
):
    """List appointments, newest first within the range."""
    start, end = bounds(from_date, to_date)

    async with async_session() as session:
        query = select(Appointment).where(Appointment.business_id == current_business_id())

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
                    Customer.phone == (normalize_phone(customer_phone) or customer_phone),
                    Customer.business_id == current_business_id(),
                )
            )
            customer = found.scalar_one_or_none()
            if not customer:
                return []
            query = query.where(Appointment.customer_id == customer.id)

        result = await session.execute(
            query.order_by(Appointment.start_time).limit(limit)
        )
        appointments = result.scalars().all()

        # A month on the calendar is hundreds of appointments. Three queries
        # for everything they name, rather than three per appointment: the
        # session keeps no lasting hold on what it loads, so looking each
        # one up separately went back to the database every time.
        loaded = {}
        for model, ids in (
            (Treatment, {a.treatment_id for a in appointments}),
            (Therapist, {a.therapist_id for a in appointments}),
            (Customer, {a.customer_id for a in appointments}),
        ):
            if ids:
                rows = await session.execute(select(model).where(model.id.in_(ids)))
                loaded.update((row.id, row) for row in rows.scalars())

        return [await serialize(session, a, loaded) for a in appointments]


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
        if body.notes:
            appointment.notes = body.notes
        if body.customer_name:
            customer = await session.get(Customer, appointment.customer_id)
            if customer:
                customer.name = body.customer_name
        if body.notes or body.customer_name:
            await session.commit()
        return await serialize(session, appointment)


@router.get("/appointments/{appointment_id}", response_model=AppointmentOut)
async def get_appointment(appointment_id: str):
    async with async_session() as session:
        try:
            appointment = await session.get(Appointment, uuid.UUID(appointment_id))
        except ValueError:
            raise HTTPException(status_code=404, detail="No such appointment")
        if not appointment or str(appointment.business_id) != current_business_id():
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
        if not appointment or str(appointment.business_id) != current_business_id():
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


@router.patch("/appointments/{appointment_id}", response_model=AppointmentOut)
async def edit_notes(appointment_id: str, body: AppointmentNotes):
    """Set or clear the clinic's private note on an appointment."""
    async with async_session() as session:
        try:
            appointment = await session.get(Appointment, uuid.UUID(appointment_id))
        except ValueError:
            raise HTTPException(status_code=404, detail="No such appointment")
        if not appointment or str(appointment.business_id) != current_business_id():
            raise HTTPException(status_code=404, detail="No such appointment")
        appointment.notes = (body.notes or "").strip() or None
        await session.commit()
        return await serialize(session, appointment)


@router.delete("/appointments/{appointment_id}")
async def cancel(appointment_id: str):
    """Cancel an appointment, keeping the row so history stays readable."""
    async with async_session() as session:
        try:
            appointment = await session.get(Appointment, uuid.UUID(appointment_id))
        except ValueError:
            raise HTTPException(status_code=404, detail="No such appointment")
        if not appointment or str(appointment.business_id) != current_business_id():
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
    from_date: str | None = None,
    to_date: str | None = None,
):
    """
    Counts and revenue over a date range, in total and per day.

    Revenue counts confirmed appointments only, so a cancelled booking does
    not appear as money taken. Every day in the range is present in by_day,
    zeros included, so a chart of it has no gaps.
    """
    start, end = bounds(from_date, to_date)
    start = start or (clinic_now().date() - dt.timedelta(days=30))
    end = end or (clinic_now().date() + dt.timedelta(days=30))
    if (end - start).days > 366:
        raise HTTPException(status_code=400, detail="Range is limited to a year")

    async with async_session() as session:
        result = await session.execute(
            select(Appointment).where(
                Appointment.business_id == current_business_id(),
                Appointment.start_time >= to_clinic_tz(
                    dt.datetime.combine(start, dt.time.min)),
                Appointment.start_time <= to_clinic_tz(
                    dt.datetime.combine(end, dt.time.max)),
            )
        )
        appointments = result.scalars().all()

        # Two queries for every name and price, instead of two per appointment.
        treatments = {t.id: t for t in (await session.execute(
            select(Treatment).where(Treatment.business_id == current_business_id()))).scalars()}
        therapists = {t.id: t for t in (await session.execute(
            select(Therapist).where(Therapist.business_id == current_business_id()))).scalars()}

    confirmed = [a for a in appointments if a.status == "confirmed"]
    cancelled = [a for a in appointments if a.status == "cancelled"]

    by_treatment: dict[str, int] = {}
    by_therapist: dict[str, int] = {}
    days = {start + dt.timedelta(days=i): [0, 0]
            for i in range((end - start).days + 1)}
    revenue = 0

    for appointment in confirmed:
        treatment = treatments.get(appointment.treatment_id)
        therapist = therapists.get(appointment.therapist_id)
        price = (treatment.price or 0) if treatment else 0
        revenue += price
        if treatment:
            by_treatment[treatment.name] = by_treatment.get(treatment.name, 0) + 1
        if therapist:
            by_therapist[therapist.name] = by_therapist.get(therapist.name, 0) + 1
        day = to_clinic_tz(appointment.start_time).date()
        if day in days:
            days[day][0] += 1
            days[day][1] += price

    decided = len(confirmed) + len(cancelled)
    return {
        "from_date": start.isoformat(),
        "to_date": end.isoformat(),
        "appointments": len(confirmed),
        "cancelled": len(cancelled),
        "revenue": revenue,
        "cancellation_rate": round(len(cancelled) / decided, 3) if decided else 0.0,
        "by_treatment": dict(sorted(by_treatment.items(), key=lambda kv: -kv[1])),
        "by_therapist": dict(sorted(by_therapist.items(), key=lambda kv: -kv[1])),
        "by_day": [
            {"date": day.isoformat(), "appointments": count, "revenue": money}
            for day, (count, money) in sorted(days.items())
        ],
    }


@router.get("/availability", response_model=AvailabilityOut)
async def availability(
    treatment_id: str,
    date: Annotated[str, Query(examples=["2027-06-25"])],
    therapist_id: str | None = None,
    exclude_appointment_id: Annotated[str | None, Query(
        description="leave this booking out, so it can be moved within its own time",
    )] = None,
):
    """
    Free start times for a treatment on a date, under the bot's own rules.

    Used when booking or moving an appointment from the dashboard, so the
    clinic is offered exactly what a customer would be.
    """
    target = parse_date(date)
    if target is None:
        raise HTTPException(status_code=400, detail=f"Could not read the date: {date}")

    try:
        treatment_uuid = uuid.UUID(treatment_id)
        excluded = uuid.UUID(exclude_appointment_id) if exclude_appointment_id else None
    except ValueError:
        raise HTTPException(status_code=400, detail="Bad id")

    async with async_session() as session:
        treatment = await session.get(Treatment, treatment_uuid)
        if not treatment or str(treatment.business_id) != current_business_id():
            raise HTTPException(status_code=404, detail="No such treatment")
        name, duration = treatment.name, treatment.duration_minutes

    # The past has no free time, whatever the schedule says.
    slots = [] if target < clinic_now().date() else await get_available_slots(
        treatment_uuid, target, exclude_appointment_id=excluded)
    if therapist_id:
        slots = [slot for slot in slots if slot["therapist_id"] == therapist_id]

    return {
        "date": target.isoformat(),
        "treatment_id": str(treatment_uuid),
        "treatment": name,
        "duration_minutes": duration,
        "slots": slots,
    }
