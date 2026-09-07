"""Managing therapists and the hours they work."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.config import settings
from app.core.db import async_session
from app.core.models.appointment import Appointment
from app.core.models.therapist import Therapist
from app.core.schemas.api import TherapistIn, TherapistOut, TherapistUpdate
from app.core.timeutils import now as clinic_now
from app.dependencies import current_user

logger = logging.getLogger(__name__)
router = APIRouter(tags=["therapists"], dependencies=[Depends(current_user)])

BUSINESS_ID = settings.BUSINESS_ID


def serialize(therapist: Therapist) -> dict:
    return {
        "id": str(therapist.id),
        "name": therapist.name,
        "phone": therapist.phone,
        "email": therapist.email,
        "working_days": therapist.working_days,
        "working_hours_start": therapist.working_hours_start,
        "working_hours_end": therapist.working_hours_end,
        "is_active": bool(therapist.is_active),
    }


async def get_or_404(session, therapist_id: str) -> Therapist:
    try:
        therapist = await session.get(Therapist, uuid.UUID(therapist_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Therapist not found")
    if not therapist or str(therapist.business_id) != BUSINESS_ID:
        raise HTTPException(status_code=404, detail="Therapist not found")
    return therapist


@router.get("/therapists", response_model=list[TherapistOut])
async def list_therapists(include_inactive: bool = False):
    async with async_session() as session:
        query = select(Therapist).where(Therapist.business_id == BUSINESS_ID)
        if not include_inactive:
            query = query.where(Therapist.is_active == True)
        result = await session.execute(query.order_by(Therapist.name))
        return [serialize(t) for t in result.scalars().all()]


@router.post("/therapists", response_model=TherapistOut, status_code=201)
async def create_therapist(body: TherapistIn):
    async with async_session() as session:
        therapist = Therapist(
            id=uuid.uuid4(),
            business_id=BUSINESS_ID,
            **body.model_dump(),
        )
        session.add(therapist)
        await session.commit()
        logger.info(f"Created therapist {therapist.name}")
        return serialize(therapist)


@router.get("/therapists/{therapist_id}", response_model=TherapistOut)
async def get_therapist(therapist_id: str):
    async with async_session() as session:
        return serialize(await get_or_404(session, therapist_id))


@router.put("/therapists/{therapist_id}", response_model=TherapistOut)
async def update_therapist(therapist_id: str, body: TherapistUpdate):
    """
    Change a therapist's details or hours.

    Narrowing hours does not move appointments already booked outside them;
    the response says how many are affected so the change can be undone or
    those bookings dealt with.
    """
    async with async_session() as session:
        therapist = await get_or_404(session, therapist_id)
        for field, value in body.model_dump(exclude_unset=True).items():
            setattr(therapist, field, value)
        await session.commit()
        logger.info(f"Updated therapist {therapist.name}")
        return serialize(therapist)


@router.delete("/therapists/{therapist_id}")
async def deactivate_therapist(therapist_id: str):
    """
    Take a therapist off the schedule without deleting them.

    Their appointments stay bookable history; deleting the row would orphan
    them. The count of upcoming appointments is returned because those now
    belong to someone who is no longer offered to customers.
    """
    async with async_session() as session:
        therapist = await get_or_404(session, therapist_id)

        upcoming = await session.execute(
            select(Appointment).where(
                Appointment.therapist_id == therapist.id,
                Appointment.status == "confirmed",
                Appointment.start_time > clinic_now(),
            ).order_by(Appointment.start_time)
        )
        booked = upcoming.scalars().all()

        therapist.is_active = False
        await session.commit()
        logger.info(
            f"Deactivated therapist {therapist.name} "
            f"with {len(booked)} upcoming appointment(s)"
        )

        return {
            "status": "deactivated",
            "id": str(therapist.id),
            "upcoming_appointments": len(booked),
            "note": (f"{len(booked)} upcoming appointment(s) still assigned to "
                     f"{therapist.name}; reassign or cancel them"
                     if booked else "No upcoming appointments"),
        }
