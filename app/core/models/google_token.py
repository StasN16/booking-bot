from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
import uuid
from app.core.models.base import TimeStampedModel

class GoogleToken(TimeStampedModel):
    __tablename__ = "google_tokens"
    
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), 
        ForeignKey("businesses.id"), 
        nullable=False,
        unique=True
    )
    encrypted_token: Mapped[str] = mapped_column(Text, nullable=False)
    
    business: Mapped["Business"] = relationship("Business")