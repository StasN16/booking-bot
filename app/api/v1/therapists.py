"""Managing therapists, the hours they work and the treatments they do."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.core.db import async_session
from app.core.tenancy import current_business_id
from app.core.models.appointment import Appointment
from app.core.models.therapist import Therapist
from app.core.models.treatment import Treatment
from app.core.schemas.api import TherapistIn, TherapistOut, TherapistUpdate
from app.core.timeutils import now as clinic_now
from app.dependencies import clinic_scope
from app.services import staffing

logger = logging.getLogger(__name__)
router = APIRouter(tags=["therapists"], dependencies=[Depends(clinic_scope)])


def serialize(therapist: Therapist, ticked: set | None = None) -> dict:
    """`ticked`: the treatments ticked for them; none means all."""
    return {
        "id": str(therapist.id),
        "name": therapist.name,
        "phone": therapist.phone,
        "email": therapist.email,
        "working_days": therapist.working_days,
        "working_hours_start": therapist.working_hours_start,
        "working_hours_end": therapist.working_hours_end,
        "is_active": bool(therapist.is_active),
        # The dashboard colours therapists in the order they joined, so
        # someone new never changes the colour everyone else already has.
        "created_at": therapist.created_at.isoformat() if therapist.created_at else None,
        "treatment_ids": sorted(str(t) for t in ticked or ()),
    }


async def checked_treatments(session, requested: list[str]) -> list[uuid.UUID]:
    """The treatment ids asked for, each one this clinic's own, or a 400."""
    try:
        wanted = {uuid.UUID(str(value)) for value in requested}
    except ValueError:
        raise HTTPException(status_code=400, detail="No such treatment")
    if not wanted:
        return []
    found = set((await session.execute(
        select(Treatment.id).where(Treatment.id.in_(wanted),
                                   Treatment.business_id == current_business_id())
    )).scalars())
    if found != wanted:
        raise HTTPException(status_code=400, detail="No such treatment")
    return sorted(wanted, key=str)


async def get_or_404(session, therapist_id: str) -> Therapist:
    try:
        therapist = await session.get(Therapist, uuid.UUID(therapist_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Therapist not found")
    if not therapist or str(therapist.business_id) != current_business_id():
        raise HTTPException(status_code=404, detail="Therapist not found")
    return therapist


@router.get("/therapists", response_model=list[TherapistOut])
async def list_therapists(include_inactive: bool = False):
    async with async_session() as session:
        query = select(Therapist).where(Therapist.business_id == current_business_id())
        if not include_inactive:
            query = query.where(Therapist.is_active == True)
        therapists = (await session.execute(query.order_by(Therapist.name))).scalars().all()
        ticked = await staffing.ticked_treatments(session, [t.id for t in therapists])
        return [serialize(t, ticked.get(t.id)) for t in therapists]


@router.post("/therapists", response_model=TherapistOut, status_code=201)
async def create_therapist(body: TherapistIn):
    async with async_session() as session:
        treatment_ids = await checked_treatments(session, body.treatment_ids)
        therapist = Therapist(
            id=uuid.uuid4(),
            business_id=current_business_id(),
            **body.model_dump(exclude={"treatment_ids"}),
        )
        session.add(therapist)
        await session.flush()  # the row first: the treatments point at it
        await staffing.set_treatments(session, therapist.id, treatment_ids)
        await session.commit()
        # The database sets created_at; read it back before serializing.
        await session.refresh(therapist)
        logger.info(f"Created therapist {therapist.name}")
        return serialize(therapist, set(treatment_ids))


@router.get("/therapists/{therapist_id}", response_model=TherapistOut)
async def get_therapist(therapist_id: str):
    async with async_session() as session:
        therapist = await get_or_404(session, therapist_id)
        ticked = await staffing.ticked_treatments(session, [therapist.id])
        return serialize(therapist, ticked.get(therapist.id))


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
        changes = body.model_dump(exclude_unset=True)
        requested = changes.pop("treatment_ids", None)
        for field, value in changes.items():
            setattr(therapist, field, value)
        if requested is not None:
            await staffing.set_treatments(session, therapist.id,
                                          await checked_treatments(session, requested))
        await session.commit()
        ticked = await staffing.ticked_treatments(session, [therapist.id])
        logger.info(f"Updated therapist {therapist.name}")
        return serialize(therapist, ticked.get(therapist.id))


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
