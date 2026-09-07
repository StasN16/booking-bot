"""
Endpoint for driving scheduled work from outside the process.

The in-process loop covers a single server. On a platform that can run a
cron job, or once more than one instance is serving, disable the loop and
call this instead so reminders fire exactly once.
"""
import hmac
import logging

from fastapi import APIRouter, Header, HTTPException

from app.config import settings
from app.services.reminders import send_due_reminders

logger = logging.getLogger(__name__)
router = APIRouter()


def authorize(token: str | None):
    """Reject anything without the shared secret, in constant time."""
    if not settings.TASKS_TOKEN:
        # No secret configured means the endpoint stays shut rather than open.
        raise HTTPException(status_code=503, detail="Task endpoint is not configured")
    if not token or not hmac.compare_digest(token, settings.TASKS_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid task token")


@router.post("/tasks/reminders")
async def run_reminders(x_task_token: str | None = Header(default=None)):
    """Send any reminders that are currently due."""
    authorize(x_task_token)
    result = await send_due_reminders()
    logger.info(f"Reminder run via HTTP: {result}")
    return result
