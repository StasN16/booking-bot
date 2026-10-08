"""
The owner's side of the service: the clinics, their WhatsApp numbers and
their logins. Nothing here is open to a clinic login.
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.core import tenancy
from app.core.db import async_session
from app.core.models.business import Business
from app.core.schemas.api import (
    ClinicIn,
    ClinicLoginIn,
    ClinicLoginOut,
    ClinicLoginUpdate,
    ClinicOut,
    ClinicUpdate,
    NewPasswordOut,
)
from app.dependencies import require_owner
from app.services import accounts

logger = logging.getLogger(__name__)
router = APIRouter(tags=["clinics (owner)"], dependencies=[Depends(require_owner)])

NUMBER_TAKEN = "Another clinic already uses that WhatsApp number"


def serialize_clinic(business: Business, logins: dict, upcoming: dict) -> dict:
    is_home = str(business.id) == settings.BUSINESS_ID
    return {
        "id": str(business.id),
        "name": business.name,
        "phone": business.phone,
        "email": business.email,
        "address": business.address,
        "working_hours_start": business.working_hours_start,
        "working_hours_end": business.working_hours_end,
        "whatsapp_phone_id": business.whatsapp_phone_id,
        "has_own_token": bool(business.whatsapp_token),
        "whatsapp_from_env": is_home and not business.whatsapp_phone_id and bool(settings.WHATSAPP_PHONE_ID),
        "is_active": bool(business.is_active),
        "is_home": is_home,
        "logins": logins.get(business.id, 0),
        "upcoming_appointments": upcoming.get(business.id, 0),
    }


def serialize_login(user) -> dict:
    return {
        "id": str(user.id),
        "business_id": str(user.business_id),
        "email": user.email,
        "name": user.name,
        "is_active": bool(user.is_active),
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


async def number_taken(session, phone_number_id: str | None, except_id=None) -> bool:
    if not phone_number_id:
        return False
    query = select(Business).where(Business.whatsapp_phone_id == phone_number_id)
    if except_id is not None:
        query = query.where(Business.id != except_id)
    return (await session.execute(query)).first() is not None


async def get_clinic(session, clinic_id: str) -> Business:
    try:
        business = await session.get(Business, uuid.UUID(clinic_id))
    except ValueError:
        business = None
    if not business:
        raise HTTPException(status_code=404, detail="No such clinic")
    return business


async def one_clinic(business: Business) -> dict:
    logins, upcoming = await accounts.clinic_counts()
    return serialize_clinic(business, logins, upcoming)


@router.get("/platform/clinics", response_model=list[ClinicOut])
async def list_clinics():
    async with async_session() as session:
        clinics = (await session.execute(select(Business).order_by(Business.name))).scalars().all()
    logins, upcoming = await accounts.clinic_counts()
    return [serialize_clinic(b, logins, upcoming) for b in clinics]


@router.post("/platform/clinics", response_model=ClinicOut, status_code=201)
async def create_clinic(body: ClinicIn):
    """A new clinic, empty: it adds its own team and treatments."""
    data = body.model_dump()
    data["whatsapp_token"] = (data.get("whatsapp_token") or "").strip() or None
    async with async_session() as session:
        if await number_taken(session, data["whatsapp_phone_id"]):
            raise HTTPException(status_code=409, detail=NUMBER_TAKEN)
        business = Business(id=uuid.uuid4(), is_active=True, **data)
        session.add(business)
        try:
            await session.commit()
        except IntegrityError:
            raise HTTPException(status_code=409, detail=NUMBER_TAKEN)
    await tenancy.refresh_channels()
    logger.info(f"Created clinic {business.id}")
    return await one_clinic(business)


@router.put("/platform/clinics/{clinic_id}", response_model=ClinicOut)
async def update_clinic(clinic_id: str, body: ClinicUpdate):
    changes = body.model_dump(exclude_unset=True)
    if "whatsapp_token" in changes:
        # Blank removes the clinic's own token, back to the server's.
        changes["whatsapp_token"] = (changes["whatsapp_token"] or "").strip() or None
    async with async_session() as session:
        business = await get_clinic(session, clinic_id)
        if "whatsapp_phone_id" in changes and await number_taken(
                session, changes["whatsapp_phone_id"], except_id=business.id):
            raise HTTPException(status_code=409, detail=NUMBER_TAKEN)
        for field, value in changes.items():
            if field in ("name", "phone", "is_active") and value is None:
                continue  # required fields are never blanked
            setattr(business, field, value)
        try:
            await session.commit()
        except IntegrityError:
            raise HTTPException(status_code=409, detail=NUMBER_TAKEN)
    await tenancy.refresh_channels()
    logger.info(f"Updated clinic {clinic_id}")
    return await one_clinic(business)


@router.get("/platform/clinics/{clinic_id}/logins", response_model=list[ClinicLoginOut])
async def list_logins(clinic_id: str):
    async with async_session() as session:
        await get_clinic(session, clinic_id)
    return [serialize_login(u) for u in await accounts.list_logins(clinic_id)]


@router.post("/platform/clinics/{clinic_id}/logins", response_model=NewPasswordOut, status_code=201)
async def create_login(clinic_id: str, body: ClinicLoginIn):
    """A login for a clinic. Its password is in the answer and nowhere else."""
    try:
        user, password = await accounts.create_login(clinic_id, body.email, body.name)
    except accounts.AccountError as e:
        status = 409 if "already" in str(e) else 404 if "No such" in str(e) else 400
        raise HTTPException(status_code=status, detail=str(e))
    return {"login": serialize_login(user), "password": password}


@router.put("/platform/logins/{login_id}", response_model=ClinicLoginOut)
async def update_login(login_id: str, body: ClinicLoginUpdate):
    try:
        user = await accounts.update_login(login_id, **body.model_dump(exclude_unset=True))
    except accounts.AccountError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return serialize_login(user)


@router.post("/platform/logins/{login_id}/password", response_model=NewPasswordOut)
async def reset_login_password(login_id: str):
    """A new password for a login, for when the clinic has lost theirs."""
    try:
        user, password = await accounts.reset_password(login_id)
    except accounts.AccountError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"login": serialize_login(user), "password": password}
