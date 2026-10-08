"""
Which clinic the current work is for.

One server runs every clinic. Each dashboard request, WhatsApp message and
reminder is handled on behalf of one of them, and everything that reads or
writes clinic data asks current_business_id() instead of holding a fixed
id. The id travels in a context variable: asyncio carries it into every
await and background task started from the same request or message, and
one request can never see another's.

Outside any request or message (scripts, the unit tests) the clinic is the
one named by BUSINESS_ID, which is also the clinic of a single-clinic
setup, so that keeps working unchanged.
"""
import contextlib
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy import select

from app.config import settings

logger = logging.getLogger(__name__)

_current: ContextVar[str | None] = ContextVar("business_id", default=None)


def current_business_id() -> str:
    """The clinic being served right now."""
    return _current.get() or settings.BUSINESS_ID


def set_current(business_id) -> None:
    """
    Serve this clinic for the rest of the current task.

    For request dependencies: each request runs in a task of its own, so
    the setting ends with the request. Elsewhere use acting_for().
    """
    _current.set(str(business_id))


@contextlib.contextmanager
def acting_for(business_id):
    """Serve this clinic inside the block, and whatever was before after it."""
    token = _current.set(str(business_id))
    try:
        yield
    finally:
        _current.reset(token)


# --- WhatsApp numbers ------------------------------------------------------------

@dataclass(frozen=True)
class Channel:
    """The WhatsApp number a clinic talks through, and the token for it."""
    phone_id: str
    token: str


# Kept in memory: a message arrives for a number and has to find its clinic
# without a database round trip, and so does every reply going out.
_channels: dict[str, Channel] = {}  # business id -> its WhatsApp number
_owners: dict[str, str] = {}  # WhatsApp phone number id -> business id
_last_refresh = 0.0

# An unknown number reloads the table at most this often, so a stream of
# messages for a number nobody has cannot turn into a stream of queries.
REFRESH_AT_MOST_EVERY_SECONDS = 30.0


async def refresh_channels() -> None:
    """Reload every active clinic's WhatsApp number from the database."""
    global _last_refresh
    from app.core.db import async_session
    from app.core.models.business import Business

    async with async_session() as session:
        rows = (await session.execute(
            select(Business).where(Business.is_active.is_(True))
        )).scalars().all()

    channels, owners = {}, {}
    for business in rows:
        if business.whatsapp_phone_id:
            channels[str(business.id)] = Channel(
                phone_id=business.whatsapp_phone_id,
                token=business.whatsapp_token or settings.WHATSAPP_TOKEN,
            )
            owners[business.whatsapp_phone_id] = str(business.id)

    _channels.clear()
    _channels.update(channels)
    _owners.clear()
    _owners.update(owners)
    _last_refresh = time.monotonic()
    logger.info(f"WhatsApp numbers loaded for {len(channels)} clinic(s)")


def channel(business_id=None) -> Channel | None:
    """
    The number a clinic sends from. A clinic without one of its own sends
    nothing: falling back to another clinic's number would message its
    customers in someone else's name. Only the clinic named by BUSINESS_ID
    may use the number in .env, as a single-clinic setup always has.
    """
    business_id = str(business_id or current_business_id())
    if business_id in _channels:
        return _channels[business_id]
    if business_id == settings.BUSINESS_ID and settings.WHATSAPP_PHONE_ID:
        return Channel(settings.WHATSAPP_PHONE_ID, settings.WHATSAPP_TOKEN)
    return None


async def business_for_phone_number(phone_number_id: str | None) -> str | None:
    """The clinic a message to this WhatsApp number is for, or None."""
    if not phone_number_id:
        # Meta always says which number a message came to. Without it there
        # is only the single-clinic answer.
        return settings.BUSINESS_ID

    if phone_number_id not in _owners and \
            time.monotonic() - _last_refresh > REFRESH_AT_MOST_EVERY_SECONDS:
        # A clinic added since the numbers were last loaded.
        await refresh_channels()

    if phone_number_id in _owners:
        return _owners[phone_number_id]
    if phone_number_id == settings.WHATSAPP_PHONE_ID:
        return settings.BUSINESS_ID
    return None


def forget_channels() -> None:
    """Empty the in-memory numbers. For tests."""
    global _last_refresh
    _channels.clear()
    _owners.clear()
    _last_refresh = 0.0
