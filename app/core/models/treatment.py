from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Boolean, Integer, ForeignKey, Index, Table, Column
from sqlalchemy.dialects.postgresql import UUID
import uuid
from app.core.models.base import Base, TimeStampedModel

# Which treatments each therapist does. A therapist with no rows here does
# every treatment, so a team that never set this up keeps working as it did;
# one with rows does only those (see app/services/staffing.py).
therapist_treatments = Table(
    "therapist_treatments",
    Base.metadata,
    Column("therapist_id", UUID(as_uuid=True),
           ForeignKey("therapists.id", ondelete="CASCADE"), primary_key=True),
    Column("treatment_id", UUID(as_uuid=True),
           ForeignKey("treatments.id", ondelete="CASCADE"), primary_key=True),
    Index("ix_therapist_treatments_treatment_id", "treatment_id"),
)

class Treatment(TimeStampedModel):
    __tablename__ = "treatments"
    
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), 
        ForeignKey("businesses.id"), 
        nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[int] = mapped_column(Integer, nullable=True)
    description: Mapped[str] = mapped_column(String(1000), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    business: Mapped["Business"] = relationship("Business", back_populates="treatments")
    therapists: Mapped[list] = relationship("Therapist", secondary="therapist_treatments", back_populates="treatments")
    appointments: Mapped[list] = relationship("Appointment", back_populates="treatment")