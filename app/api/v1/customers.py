"""Reading and lightly editing customer records."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from app.config import settings
from app.core.db import async_session
from app.core.models.appointment import Appointment
from app.core.models.customer import Customer
from app.core.schemas.api import CustomerOut, CustomerUpdate
from app.dependencies import current_user

logger = logging.getLogger(__name__)
router = APIRouter(tags=["customers"], dependencies=[Depends(current_user)])

BUSINESS_ID = settings.BUSINESS_ID


def serialize(customer: Customer, appointment_count: int = 0) -> dict:
    return {
        "id": str(customer.id),
        "name": customer.name,
        "phone": customer.phone,
        "language": customer.language,
        "is_blocked": bool(customer.is_blocked),
        "conversation_state": customer.conversation_state,
        "appointments": appointment_count,
    }


async def get_or_404(session, customer_id: str) -> Customer:
    try:
        customer = await session.get(Customer, uuid.UUID(customer_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="No such customer")
    if not customer or str(customer.business_id) != BUSINESS_ID:
        raise HTTPException(status_code=404, detail="No such customer")
    return customer


@router.get("/customers", response_model=list[CustomerOut])
async def list_customers(
    search: str | None = Query(default=None, description="name or phone"),
    limit: int = Query(default=200, ge=1, le=1000),
):
    async with async_session() as session:
        query = select(Customer).where(Customer.business_id == BUSINESS_ID)
        if search:
            pattern = f"%{search}%"
            query = query.where(
                Customer.phone.ilike(pattern) | Customer.name.ilike(pattern)
            )

        result = await session.execute(query.order_by(Customer.phone).limit(limit))
        customers = result.scalars().all()

        # One grouped query rather than one per customer.
        counts = dict(
            (await session.execute(
                select(Appointment.customer_id, func.count(Appointment.id))
                .where(Appointment.business_id == BUSINESS_ID)
                .group_by(Appointment.customer_id)
            )).all()
        )

        return [serialize(c, counts.get(c.id, 0)) for c in customers]


@router.get("/customers/{customer_id}", response_model=CustomerOut)
async def get_customer(customer_id: str):
    async with async_session() as session:
        customer = await get_or_404(session, customer_id)
        count = await session.execute(
            select(func.count(Appointment.id))
            .where(Appointment.customer_id == customer.id)
        )
        return serialize(customer, count.scalar() or 0)


@router.put("/customers/{customer_id}", response_model=CustomerOut)
async def update_customer(customer_id: str, body: CustomerUpdate):
    """
    Set a customer's name, or block them.

    Blocking stops reminders reaching them; it does not delete anything and
    does not cancel appointments they already hold.
    """
    async with async_session() as session:
        customer = await get_or_404(session, customer_id)
        for field, value in body.model_dump(exclude_unset=True).items():
            setattr(customer, field, value)
        await session.commit()
        logger.info(f"Updated customer {customer.id}")

        count = await session.execute(
            select(func.count(Appointment.id))
            .where(Appointment.customer_id == customer.id)
        )
        return serialize(customer, count.scalar() or 0)
