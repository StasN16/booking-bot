"""The clinic's own details: name, contact, opening hours."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.core.db import async_session
from app.core.tenancy import current_business_id
from app.core.models.business import Business
from app.core.schemas.api import BusinessOut, BusinessUpdate
from app.dependencies import clinic_scope

logger = logging.getLogger(__name__)
router = APIRouter(tags=["business"], dependencies=[Depends(clinic_scope)])


def serialize(business: Business) -> dict:
    # The row also holds WhatsApp credentials. They are never sent anywhere.
    return {
        "id": str(business.id),
        "name": business.name,
        "phone": business.phone,
        "email": business.email,
        "address": business.address,
        "working_hours_start": business.working_hours_start,
        "working_hours_end": business.working_hours_end,
        # Dates on the dashboard are the clinic's, wherever it is opened from.
        "timezone": settings.TIMEZONE,
    }


async def load(session) -> Business:
    business = await session.get(Business, uuid.UUID(current_business_id()))
    if not business:
        # The empty-clinic failure: the configured id matches no row.
        raise HTTPException(
            status_code=404,
            detail="No business matches BUSINESS_ID; check .env against the data",
        )
    return business


@router.get("/business", response_model=BusinessOut)
async def get_business():
    async with async_session() as session:
        return serialize(await load(session))


@router.put("/business", response_model=BusinessOut)
async def update_business(body: BusinessUpdate):
    async with async_session() as session:
        business = await load(session)
        for field, value in body.model_dump(exclude_unset=True).items():
            setattr(business, field, value)
        await session.commit()
        logger.info("Updated business details")
        return serialize(business)
