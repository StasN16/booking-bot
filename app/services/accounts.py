"""
Clinic logins: making them, checking them, and changing their passwords.

A login belongs to one clinic. The owner gives each new login a password:
the start password from .env (CLINIC_START_PASSWORD), or a random one when
none is set. It is shown to the owner to pass on, and afterwards only its
hash exists. Either way it is only a start: until the user chooses their
own password, they may do nothing else.
"""
import logging
import re
import uuid

from sqlalchemy import func, select

from app.config import settings
from app.core.db import async_session
from app.core.models.appointment import Appointment
from app.core.models.business import Business
from app.core.models.user import User
from app.core.passwords import (
    DECOY_HASH,
    MIN_LENGTH,
    generate_password,
    hash_password,
    verify_password,
)
from app.core.timeutils import now as clinic_now

logger = logging.getLogger(__name__)

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Signed in with a password the owner gave: only choosing one's own is open.
CHOOSE_PASSWORD = "Choose your own password first"


class AccountError(ValueError):
    """A request about logins that cannot be done, with the reason to show."""


def given_password() -> str:
    """The password the owner hands a login: the start password, if there is one."""
    return settings.CLINIC_START_PASSWORD or generate_password()


def normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


def _uuid(value) -> uuid.UUID | None:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except ValueError:
        return None


async def business_exists(business_id) -> bool:
    key = _uuid(business_id)
    if key is None:
        return False
    async with async_session() as session:
        return await session.get(Business, key) is not None


async def authenticate(email: str, password: str) -> User | None:
    """The login with this email and password, if it may sign in."""
    email = normalize_email(email)
    async with async_session() as session:
        found = (await session.execute(
            select(User, Business)
            .join(Business, Business.id == User.business_id)
            .where(User.email == email)
        )).first()

        if not found:
            verify_password(password, DECOY_HASH)
            return None
        user, business = found
        if not verify_password(password, user.password_hash):
            return None
        if not user.is_active or not business.is_active:
            return None

        user.last_login_at = clinic_now()
        await session.commit()
        return user


async def login_problem(user_id, business_id, *, choosing_password: bool = False) -> str | None:
    """
    Why this login may not be used right now, or None if it may.
    `choosing_password` is for the requests a login still holding the
    owner's password may make: who am I, and change my password.
    """
    key = _uuid(user_id)
    if key is None:
        return "Invalid token"
    async with async_session() as session:
        found = (await session.execute(
            select(User, Business)
            .join(Business, Business.id == User.business_id)
            .where(User.id == key)
        )).first()
    if not found:
        return "This login no longer exists"
    user, business = found
    if str(user.business_id) != str(business_id):
        return "Invalid token"
    if not user.is_active:
        return "This login has been turned off"
    if not business.is_active:
        return "This clinic is not active"
    if user.must_change_password and not choosing_password:
        return CHOOSE_PASSWORD
    return None


async def get_login(user_id) -> User | None:
    key = _uuid(user_id)
    if key is None:
        return None
    async with async_session() as session:
        return await session.get(User, key)


async def list_logins(business_id) -> list[User]:
    async with async_session() as session:
        return list((await session.execute(
            select(User).where(User.business_id == _uuid(business_id)).order_by(User.email)
        )).scalars().all())


async def create_login(business_id, email: str, name: str | None = None) -> tuple[User, str]:
    """A new login for a clinic, and the password to pass on to it."""
    email = normalize_email(email)
    if not EMAIL.match(email):
        raise AccountError("That does not look like an email address")
    if not await business_exists(business_id):
        raise AccountError("No such clinic")

    password = given_password()
    async with async_session() as session:
        if (await session.execute(select(User).where(User.email == email))).first():
            raise AccountError("That email already has a login")
        user = User(
            id=uuid.uuid4(),
            business_id=_uuid(business_id),
            email=email,
            name=(name or "").strip() or None,
            password_hash=hash_password(password),
            must_change_password=True,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    logger.info(f"Created a login for clinic {business_id}")
    return user, password


async def reset_password(user_id) -> tuple[User, str]:
    """Give a login a new password to pass on, for when it lost its own."""
    password = given_password()
    async with async_session() as session:
        user = await session.get(User, _uuid(user_id)) if _uuid(user_id) else None
        if not user:
            raise AccountError("No such login")
        user.password_hash = hash_password(password)
        user.must_change_password = True
        await session.commit()
        await session.refresh(user)
    logger.info(f"Reset the password of login {user_id}")
    return user, password


async def update_login(user_id, **changes) -> User:
    """Change a login's name or switch it on or off."""
    async with async_session() as session:
        user = await session.get(User, _uuid(user_id)) if _uuid(user_id) else None
        if not user:
            raise AccountError("No such login")
        if "name" in changes:
            user.name = (changes["name"] or "").strip() or None
        if "is_active" in changes and changes["is_active"] is not None:
            user.is_active = bool(changes["is_active"])
        await session.commit()
        await session.refresh(user)
        return user


async def change_password(user_id, current: str, new: str) -> None:
    """A clinic choosing its own password, which needs the current one."""
    if len(new or "") < MIN_LENGTH:
        raise AccountError(f"The new password needs at least {MIN_LENGTH} characters")
    if settings.CLINIC_START_PASSWORD and new == settings.CLINIC_START_PASSWORD:
        raise AccountError("That is the start password everyone is given. Choose one of your own")
    if new == current:
        raise AccountError("The new password is the same as the current one")
    async with async_session() as session:
        user = await session.get(User, _uuid(user_id)) if _uuid(user_id) else None
        if not user or not verify_password(current, user.password_hash):
            raise AccountError("The current password is not right")
        user.password_hash = hash_password(new)
        user.must_change_password = False
        await session.commit()
    logger.info(f"Login {user_id} changed its password")


async def give_everyone(password: str) -> list[str]:
    """
    Every clinic login gets this password, to be replaced at its next
    sign-in. For a one-off start (scripts/start_password.py --everyone).
    Returns the emails changed.
    """
    async with async_session() as session:
        users = (await session.execute(select(User).order_by(User.email))).scalars().all()
        for user in users:
            user.password_hash = hash_password(password)  # each with its own salt
            user.must_change_password = True
        await session.commit()
        return [user.email for user in users]


async def clinic_counts() -> tuple[dict, dict]:
    """Per clinic: how many logins, and how many appointments are coming up."""
    async with async_session() as session:
        logins = dict((await session.execute(
            select(User.business_id, func.count(User.id)).group_by(User.business_id)
        )).all())
        upcoming = dict((await session.execute(
            select(Appointment.business_id, func.count(Appointment.id))
            .where(Appointment.status == "confirmed", Appointment.start_time > clinic_now())
            .group_by(Appointment.business_id)
        )).all())
    return logins, upcoming
