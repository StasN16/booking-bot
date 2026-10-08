from datetime import datetime
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models.base import TimeStampedModel


class User(TimeStampedModel):
    """
    A clinic's login to the dashboard. It sees that clinic and nothing else.

    The owner of the whole service has no row here: they sign in with the
    password in .env (ADMIN_PASSWORD) and may look at any clinic.
    """
    __tablename__ = "users"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id"),
        nullable=False,
        index=True,
    )
    # Stored lower case, so signing in does not depend on how it was typed.
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=True)
    # Never the password itself: see app/core/passwords.py.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
