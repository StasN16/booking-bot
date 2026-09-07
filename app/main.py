import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.api.v1 import tasks, webhook
from app.services.reminders import send_due_reminders

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def reminder_loop():
    """Check for due reminders on a fixed interval until the server stops."""
    interval = max(1, settings.REMINDER_POLL_MINUTES) * 60
    logger.info(f"Reminder loop started, every {settings.REMINDER_POLL_MINUTES} min")
    while True:
        try:
            await send_due_reminders()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Never let one bad run stop the loop for good.
            logger.exception(f"Reminder run failed: {e}")
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = None
    if settings.REMINDERS_ENABLED:
        task = asyncio.create_task(reminder_loop())
    else:
        logger.info("Reminder loop disabled; drive /api/v1/tasks/reminders instead")

    yield

    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        logger.info("Reminder loop stopped")


app = FastAPI(title="Booking Bot", version="0.1.0", lifespan=lifespan)

app.include_router(webhook.router, prefix="/api/v1")
app.include_router(tasks.router, prefix="/api/v1")


@app.get("/")
async def root():
    return {"status": "ok", "message": "Bot is running!"}


@app.get("/health")
async def health():
    return {"status": "healthy"}
