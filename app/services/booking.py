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

logger = logging.getLogger(__name__)

engine = create_async_engine(settings.DATABASE_URL)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

BUSINESS_ID = "550e8400-e29b-41d4-a716-446655440000"

async def get_or_create_customer(phone: str, name: str = None) -> Customer:
    """Get existing customer or create new one"""
    async with async_session() as session:
        result = await session.execute(
            select(Customer).where(
                Customer.phone == phone,
                Customer.business_id == BUSINESS_ID
            )
        )
        customer = result.scalar_one_or_none()
        
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
            # Get customer
            result = await session.execute(
                select(Customer).where(
                    Customer.phone == customer_phone,
                    Customer.business_id == BUSINESS_ID
                )
            )
            customer = result.scalar_one_or_none()
            
            if not customer:
                customer = Customer(
                    id=uuid.uuid4(),
                    business_id=BUSINESS_ID,
                    phone=customer_phone,
                    conversation_state="idle"
                )
                session.add(customer)
                await session.flush()

            # Get treatment
            result = await session.execute(
                select(Treatment).where(
                    Treatment.business_id == BUSINESS_ID,
                    Treatment.name.ilike(f"%{treatment_name}%"),
                    Treatment.is_active == True
                )
            )
            treatment = result.scalar_one_or_none()
            
            if not treatment:
                return {"success": False, "error": "Treatment not found"}

            # Get therapist
            result = await session.execute(
                select(Therapist).where(
                    Therapist.business_id == BUSINESS_ID,
                    Therapist.name.ilike(f"%{therapist_name}%"),
                    Therapist.is_active == True
                )
            )
            therapist = result.scalar_one_or_none()
            
            if not therapist:
                # Use first available therapist
                result = await session.execute(
                    select(Therapist).where(
                        Therapist.business_id == BUSINESS_ID,
                        Therapist.is_active == True
                    )
                )
                therapist = result.scalars().first()
            
            if not therapist:
                return {"success": False, "error": "No therapist available"}

            # Parse date and time
            date_obj = datetime.strptime(appointment_date, "%Y-%m-%d")
            time_obj = datetime.strptime(appointment_time, "%H:%M")
            start_time = date_obj.replace(
                hour=time_obj.hour,
                minute=time_obj.minute
            )
            end_time = start_time + timedelta(minutes=treatment.duration_minutes)

            # Check for conflicting appointments
            conflict_result = await session.execute(
                select(Appointment).where(
                    Appointment.therapist_id == therapist.id,
                    Appointment.status == "confirmed",
                    Appointment.start_time < end_time,
                    Appointment.end_time > start_time,
                )
            )
            conflict = conflict_result.scalars().first()
            if conflict:
                return {"success": False, "error": "slot_taken", "message": "השעה כבר תפוסה. בחר שעה אחרת."}

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

            logger.info(f"Created appointment for {customer_phone}: {treatment_name} on {appointment_date} at {appointment_time}")
            
            return {
                "success": True,
                "appointment_id": str(appointment.id),
                "treatment": treatment.name,
                "therapist": therapist.name,
                "date": appointment_date,
                "time": appointment_time,
                "duration": treatment.duration_minutes,
                "price": treatment.price
            }

        except Exception as e:
            logger.error(f"Error creating appointment: {e}")
            await session.rollback()
            return {"success": False, "error": str(e)}

async def cancel_appointment(customer_phone: str, appointment_id: str = None) -> dict:
    """Cancel an appointment"""
    async with async_session() as session:
        try:
            if appointment_id:
                appointment = await session.get(Appointment, appointment_id)
            else:
                # Get most recent upcoming appointment
                result = await session.execute(
                    select(Customer).where(
                        Customer.phone == customer_phone,
                        Customer.business_id == BUSINESS_ID
                    )
                )
                customer = result.scalar_one_or_none()
                
                if not customer:
                    return {"success": False, "error": "Customer not found"}
                
                result = await session.execute(
                    select(Appointment).where(
                        Appointment.customer_id == customer.id,
                        Appointment.status == "confirmed",
                        Appointment.start_time > datetime.now()
                    ).order_by(Appointment.start_time)
                )
                appointment = result.scalars().first()
            
            if not appointment:
                return {"success": False, "error": "No upcoming appointment found"}
            
            appointment.status = "cancelled"
            await session.commit()
            
            return {
                "success": True,
                "message": "Appointment cancelled successfully"
            }

        except Exception as e:
            logger.error(f"Error cancelling appointment: {e}")
            await session.rollback()
            return {"success": False, "error": str(e)}

async def get_customer_appointments(customer_phone: str) -> list:
    """Get upcoming appointments for a customer"""
    async with async_session() as session:
        result = await session.execute(
            select(Customer).where(
                Customer.phone == customer_phone,
                Customer.business_id == BUSINESS_ID
            )
        )
        customer = result.scalar_one_or_none()
        
        if not customer:
            return []
        
        result = await session.execute(
            select(Appointment).where(
                Appointment.customer_id == customer.id,
                Appointment.status == "confirmed",
                Appointment.start_time > datetime.now()
            ).order_by(Appointment.start_time)
        )
        appointments = result.scalars().all()
        
        result_list = []
        for app in appointments:
            treatment = await session.get(Treatment, app.treatment_id)
            therapist = await session.get(Therapist, app.therapist_id)
            result_list.append({
                "id": str(app.id),
                "treatment": treatment.name if treatment else "Unknown",
                "therapist": therapist.name if therapist else "Unknown",
                "date": app.start_time.strftime("%Y-%m-%d"),
                "time": app.start_time.strftime("%H:%M"),
                "status": app.status
            })
        
        return result_list