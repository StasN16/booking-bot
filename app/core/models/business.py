from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Boolean, Time
from app.core.models.base import TimeStampedModel

class Business(TimeStampedModel):
    __tablename__ = "businesses"
    
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=True)
    address: Mapped[str] = mapped_column(String(500), nullable=True)
    whatsapp_token: Mapped[str] = mapped_column(String(500), nullable=True)
    whatsapp_phone_id: Mapped[str] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    working_hours_start: Mapped[str] = mapped_column(String(10), nullable=True)
    working_hours_end: Mapped[str] = mapped_column(String(10), nullable=True)
    
    therapists: Mapped[list] = relationship("Therapist", back_populates="business")
    treatments: Mapped[list] = relationship("Treatment", back_populates="business")
    customers: Mapped[list] = relationship("Customer", back_populates="business")