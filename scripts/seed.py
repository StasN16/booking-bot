import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.config import settings
from app.core.models.business import Business
from app.core.models.therapist import Therapist
from app.core.models.treatment import Treatment, therapist_treatments
from app.core.models.customer import Customer
from app.core.models.appointment import Appointment
from app.core.models.google_token import GoogleToken
import uuid

def seed():
    # Use sync driver for seed script
    sync_url = settings.DATABASE_URL.replace("+asyncpg", "")
    engine = create_engine(sync_url)
    Session = sessionmaker(engine)
    
    with Session() as session:
        # Create business. The id has to be the one the services query by,
        # otherwise the bot starts up against an apparently empty clinic.
        business = Business(
            id=uuid.UUID(settings.BUSINESS_ID),
            name="מרכז טיפולים דנה",
            phone="0501234567",
            email="dana@clinic.com",
            address="רחוב הרצל 10, תל אביב",
            working_hours_start="09:00",
            working_hours_end="20:00",
            is_active=True
        )
        session.add(business)
        session.flush()
        
        print(f"Created business: {business.name} (ID: {business.id})")

        # Create therapists
        therapist1 = Therapist(
            id=uuid.uuid4(),
            business_id=business.id,
            name="נועה כהן",
            phone="0521234567",
            is_active=True,
            working_days="Sunday,Monday,Tuesday,Wednesday,Thursday",
            working_hours_start="09:00",
            working_hours_end="18:00"
        )
        
        therapist2 = Therapist(
            id=uuid.uuid4(),
            business_id=business.id,
            name="יעל לוי",
            phone="0531234567",
            is_active=True,
            working_days="Monday,Tuesday,Wednesday,Thursday,Friday",
            working_hours_start="10:00",
            working_hours_end="20:00"
        )
        
        session.add(therapist1)
        session.add(therapist2)
        session.flush()
        
        print(f"Created therapists: {therapist1.name}, {therapist2.name}")

        # Create treatments
        treatment1 = Treatment(
            id=uuid.uuid4(),
            business_id=business.id,
            name="עיסוי שוודי",
            duration_minutes=60,
            price=280,
            description="עיסוי גוף מלא מרגיע",
            is_active=True
        )
        
        treatment2 = Treatment(
            id=uuid.uuid4(),
            business_id=business.id,
            name="טיפול פנים",
            duration_minutes=45,
            price=220,
            description="טיפול פנים מעמיק ומרענן",
            is_active=True
        )
        
        treatment3 = Treatment(
            id=uuid.uuid4(),
            business_id=business.id,
            name="רפלקסולוגיה",
            duration_minutes=45,
            price=200,
            description="טיפול רפלקסולוגיה ברגליים",
            is_active=True
        )

        treatment4 = Treatment(
            id=uuid.uuid4(),
            business_id=business.id,
            name="עיסוי רקמות עמוק",
            duration_minutes=60,
            price=320,
            description="עיסוי אינטנסיבי לשחרור מתחים",
            is_active=True
        )
        
        session.add(treatment1)
        session.add(treatment2)
        session.add(treatment3)
        session.add(treatment4)
        session.flush()
        
        print(f"Created treatments: {treatment1.name}, {treatment2.name}, {treatment3.name}, {treatment4.name}")

        session.commit()
        print("\nDatabase seeded successfully!")
        print(f"Business ID: {business.id}")

if __name__ == "__main__":
    seed()