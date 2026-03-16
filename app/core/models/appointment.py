from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Boolean, Integer, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import UUID
import uuid
from datetime import datetime
from app.core.models.base import TimeStampedModel

class Appointment(TimeStampedModel):
    __tablename__ = "appointments"
    
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), 
        ForeignKey("businesses.id"), 
        nullable=False
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), 
        ForeignKey("customers.id"), 
        nullable=False
    )
    therapist_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), 
        ForeignKey("therapists.id"), 
        nullable=False
    )
    treatment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), 
        ForeignKey("treatments.id"), 
        nullable=False
    )
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="confirmed")
    notes: Mapped[str] = mapped_column(String(1000), nullable=True)
    reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    
    business: Mapped["Business"] = relationship("Business")
    customer: Mapped["Customer"] = relationship("Customer", back_populates="appointments")
    therapist: Mapped["Therapist"] = relationship("Therapist", back_populates="appointments")
    treatment: Mapped["Treatment"] = relationship("Treatment", back_populates="appointments")