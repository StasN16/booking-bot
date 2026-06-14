import logging
from datetime import datetime, date, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from app.config import settings
from app.core.models.business import Business
from app.core.models.therapist import Therapist
from app.core.models.treatment import Treatment
from app.core.models.appointment import Appointment

logger = logging.getLogger(__name__)

engine = create_async_engine(settings.DATABASE_URL)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

BUSINESS_ID = "550e8400-e29b-41d4-a716-446655440000"

async def get_treatments() -> list:
    """Get all active treatments"""
    async with async_session() as session:
        result = await session.execute(
            select(Treatment).where(
                Treatment.business_id == BUSINESS_ID,
                Treatment.is_active == True
            )
        )
        treatments = result.scalars().all()
        return [
            {
                "id": str(t.id),
                "name": t.name,
                "duration_minutes": t.duration_minutes,
                "price": t.price,
                "description": t.description
            }
            for t in treatments
        ]

async def get_therapists() -> list:
    """Get all active therapists"""
    async with async_session() as session:
        result = await session.execute(
            select(Therapist).where(
                Therapist.business_id == BUSINESS_ID,
                Therapist.is_active == True
            )
        )
        therapists = result.scalars().all()
        return [
            {
                "id": str(t.id),
                "name": t.name,
                "working_days": t.working_days,
                "working_hours_start": t.working_hours_start,
                "working_hours_end": t.working_hours_end
            }
            for t in therapists
        ]

async def get_available_slots(treatment_id: str, target_date: date) -> list:
    """Get available time slots for a treatment on a specific date"""
    async with async_session() as session:
        # Get treatment duration
        treatment = await session.get(Treatment, treatment_id)
        if not treatment:
            return []
        
        duration = treatment.duration_minutes
        day_name = target_date.strftime("%A")  # Monday, Tuesday, etc.
        
        # Get therapists working on that day
        result = await session.execute(
            select(Therapist).where(
                Therapist.business_id == BUSINESS_ID,
                Therapist.is_active == True
            )
        )
        therapists = result.scalars().all()
        available_therapists = [
            t for t in therapists 
            if day_name in t.working_days
        ]
        
        if not available_therapists:
            return []
        
        # Get existing appointments for that date
        start_of_day = datetime.combine(target_date, datetime.min.time())
        end_of_day = datetime.combine(target_date, datetime.max.time())
        
        result = await session.execute(
            select(Appointment).where(
                Appointment.business_id == BUSINESS_ID,
                Appointment.start_time >= start_of_day,
                Appointment.start_time <= end_of_day,
                Appointment.status == "confirmed"
            )
        )
        existing_appointments = result.scalars().all()
        
        # Calculate available slots
        available_slots = []
        
        for therapist in available_therapists:
            start_hour = int(therapist.working_hours_start.split(":")[0])
            end_hour = int(therapist.working_hours_end.split(":")[0])
            
            current_time = datetime.combine(target_date, datetime.min.time().replace(hour=start_hour))
            end_time = datetime.combine(target_date, datetime.min.time().replace(hour=end_hour))
            
            while current_time + timedelta(minutes=duration) <= end_time:
                slot_end = current_time + timedelta(minutes=duration)
                
                # Check if slot is taken
                is_taken = any(
                    app.therapist_id == therapist.id and
                    not (slot_end <= app.start_time or current_time >= app.end_time)
                    for app in existing_appointments
                )
                
                if not is_taken:
                    available_slots.append({
                        "time": current_time.strftime("%H:%M"),
                        "therapist_id": str(therapist.id),
                        "therapist_name": therapist.name
                    })
                
                current_time += timedelta(minutes=30)
        
        return available_slots

async def get_treatments_summary() -> str:
    """Get a text summary of treatments for the bot"""
    treatments = await get_treatments()
    if not treatments:
        return "אין טיפולים זמינים כרגע"
    
    lines = []
    for t in treatments:
        lines.append(f"- {t['name']}: {t['duration_minutes']} דקות, {t['price']}₪")
    
    return "\n".join(lines)