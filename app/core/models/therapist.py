from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
import uuid
from app.core.models.base import TimeStampedModel

class Therapist(TimeStampedModel):
    __tablename__ = "therapists"
    
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), 
        ForeignKey("businesses.id"), 
        nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=True)
    email: Mapped[str] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    working_days: Mapped[str] = mapped_column(String(50), nullable=True)
    working_hours_start: Mapped[str] = mapped_column(String(10), nullable=True)
    working_hours_end: Mapped[str] = mapped_column(String(10), nullable=True)
    
    business: Mapped["Business"] = relationship("Business", back_populates="therapists")
    treatments: Mapped[list] = relationship("Treatment", secondary="therapist_treatments", back_populates="therapists")
    appointments: Mapped[list] = relationship("Appointment", back_populates="therapist")