from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
import uuid
from app.core.models.base import TimeStampedModel

class Customer(TimeStampedModel):
    __tablename__ = "customers"
    
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), 
        ForeignKey("businesses.id"), 
        nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=True)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    conversation_state: Mapped[str] = mapped_column(String(50), nullable=True)
    conversation_data: Mapped[str] = mapped_column(String(5000), nullable=True)
    
    business: Mapped["Business"] = relationship("Business", back_populates="customers")
    appointments: Mapped[list] = relationship("Appointment", back_populates="customer")