"""Managing the treatments the clinic offers."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.config import settings
from app.core.db import async_session
from app.core.models.appointment import Appointment
from app.core.models.treatment import Treatment
from app.core.schemas.api import TreatmentIn, TreatmentOut, TreatmentUpdate
from app.core.timeutils import now as clinic_now
from app.dependencies import current_user

logger = logging.getLogger(__name__)
router = APIRouter(tags=["treatments"], dependencies=[Depends(current_user)])

BUSINESS_ID = settings.BUSINESS_ID


def serialize(treatment: Treatment) -> dict:
    return {
        "id": str(treatment.id),
        "name": treatment.name,
        "duration_minutes": treatment.duration_minutes,
        "price": treatment.price,
        "description": treatment.description,
        "is_active": bool(treatment.is_active),
    }


async def get_or_404(session, treatment_id: str) -> Treatment:
    try:
        treatment = await session.get(Treatment, uuid.UUID(treatment_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Treatment not found")
    if not treatment or str(treatment.business_id) != BUSINESS_ID:
        raise HTTPException(status_code=404, detail="Treatment not found")
    return treatment


@router.get("/treatments", response_model=list[TreatmentOut])
async def list_treatments(include_inactive: bool = False):
    async with async_session() as session:
        query = select(Treatment).where(Treatment.business_id == BUSINESS_ID)
        if not include_inactive:
            query = query.where(Treatment.is_active == True)
        result = await session.execute(query.order_by(Treatment.name))
        return [serialize(t) for t in result.scalars().all()]


@router.post("/treatments", response_model=TreatmentOut, status_code=201)
async def create_treatment(body: TreatmentIn):
    async with async_session() as session:
        treatment = Treatment(
            id=uuid.uuid4(),
            business_id=BUSINESS_ID,
            **body.model_dump(),
        )
        session.add(treatment)
        await session.commit()
        logger.info(f"Created treatment {treatment.name}")
        return serialize(treatment)


@router.get("/treatments/{treatment_id}", response_model=TreatmentOut)
async def get_treatment(treatment_id: str):
    async with async_session() as session:
        return serialize(await get_or_404(session, treatment_id))


@router.put("/treatments/{treatment_id}", response_model=TreatmentOut)
async def update_treatment(treatment_id: str, body: TreatmentUpdate):
    async with async_session() as session:
        treatment = await get_or_404(session, treatment_id)
        for field, value in body.model_dump(exclude_unset=True).items():
            setattr(treatment, field, value)
        await session.commit()
        logger.info(f"Updated treatment {treatment.name}")
        return serialize(treatment)


@router.delete("/treatments/{treatment_id}")
async def deactivate_treatment(treatment_id: str):
    """
    Retire a treatment without deleting it.

    Appointments reference the treatment, so removing the row would take
    their history with it. Deactivating keeps the past readable while
    hiding it from anything new.
    """
    async with async_session() as session:
        treatment = await get_or_404(session, treatment_id)

        upcoming = await session.execute(
            select(Appointment).where(
                Appointment.treatment_id == treatment.id,
                Appointment.status == "confirmed",
                Appointment.start_time > clinic_now(),
            )
        )
        booked = len(upcoming.scalars().all())

        treatment.is_active = False
        await session.commit()
        logger.info(f"Deactivated treatment {treatment.name}")

        return {
            "status": "deactivated",
            "id": str(treatment.id),
            "upcoming_appointments": booked,
            "note": ("Existing appointments are unaffected"
                     if booked else "No upcoming appointments"),
        }
