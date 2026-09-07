"""
Conversation state, kept in the database.

State used to live in module-level dictionaries, so restarting the server
dropped every conversation midway: someone who had picked a treatment and a
date would be answered as a stranger. The customers table already carried
conversation_state and conversation_data for this, unused.

conversation_data is a 5000 character column, so the history is trimmed to
fit rather than allowed to overflow and fail the write.
"""
import json
import logging
import uuid

from sqlalchemy import select

from app.config import settings
from app.core.db import async_session
from app.core.enums import ConversationState
from app.core.models.customer import Customer

logger = logging.getLogger(__name__)

BUSINESS_ID = settings.BUSINESS_ID

MAX_HISTORY_MESSAGES = 20
# Under the column's 5000 so a long booking context cannot push it over.
MAX_DATA_CHARS = 4500


def pack(history: list, booking: dict) -> str:
    """
    Serialize a conversation, dropping the oldest turns until it fits.

    Losing the start of a long conversation is survivable; failing the write
    and losing all of it is not.
    """
    history = list(history)[-MAX_HISTORY_MESSAGES:]

    while True:
        blob = json.dumps({"history": history, "booking": booking}, ensure_ascii=False)
        if len(blob) <= MAX_DATA_CHARS or not history:
            return blob
        # Turns are stored in user/assistant pairs, so drop them in pairs.
        history = history[2:]


def unpack(blob: str) -> tuple[list, dict]:
    """Read back what pack() wrote, tolerating anything unexpected."""
    if not blob:
        return [], {}
    try:
        data = json.loads(blob)
    except (ValueError, TypeError):
        logger.warning("Ignoring unreadable conversation_data")
        return [], {}
    if not isinstance(data, dict):
        return [], {}

    history = data.get("history")
    booking = data.get("booking")
    return (
        history if isinstance(history, list) else [],
        booking if isinstance(booking, dict) else {},
    )


async def load(phone: str) -> dict:
    """Everything needed to continue a conversation with this number."""
    async with async_session() as session:
        customer = await _get(session, phone)
        if not customer:
            return {"state": ConversationState.IDLE, "history": [], "booking": {},
                    "language": None}

        history, booking = unpack(customer.conversation_data)
        return {
            "state": customer.conversation_state or ConversationState.IDLE,
            "history": history,
            "booking": booking,
            "language": customer.language,
        }


async def save(phone: str, state: str, history: list, booking: dict,
               language: str = None):
    """Persist the conversation, creating the customer on first contact."""
    async with async_session() as session:
        customer = await _get(session, phone)
        if not customer:
            customer = Customer(
                id=uuid.uuid4(),
                business_id=BUSINESS_ID,
                phone=phone,
            )
            session.add(customer)

        # str() on a (str, Enum) member gives "ConversationState.IDLE" rather
        # than "idle", which would never match on the way back in.
        customer.conversation_state = getattr(state, "value", state)
        customer.conversation_data = pack(history, booking)
        if language in ("he", "en", "ru"):
            customer.language = language

        await session.commit()


async def clear_booking(phone: str):
    """Forget the in-progress booking, keeping the conversation history."""
    async with async_session() as session:
        customer = await _get(session, phone)
        if not customer:
            return
        history, _ = unpack(customer.conversation_data)
        customer.conversation_data = pack(history, {})
        await session.commit()


async def _get(session, phone: str):
    result = await session.execute(
        select(Customer).where(
            Customer.phone == phone,
            Customer.business_id == BUSINESS_ID,
        )
    )
    return result.scalar_one_or_none()
