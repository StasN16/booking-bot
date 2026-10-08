import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.core import tenancy
from app.api.v1 import (
    appointments,
    auth,
    business,
    customers,
    platform,
    tasks,
    therapists,
    treatments,
    webhook,
)
from app.services import audit_watch
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


async def audit_loop():
    """Check the audit trail on an interval and log anything wrong with it."""
    interval = max(1, settings.AUDIT_WATCH_MINUTES) * 60
    # Findings already in the log were seen before this restart.
    known = audit_watch.prime()
    logger.info(
        f"Audit watch started, every {settings.AUDIT_WATCH_MINUTES} min "
        f"({known} existing traces marked as seen)"
    )
    while True:
        await asyncio.sleep(interval)
        try:
            audit_watch.check_recent()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"Audit watch failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = []

    # Which WhatsApp number belongs to which clinic. Messages still find
    # their clinic if this fails now, by loading the numbers on demand.
    try:
        await tenancy.refresh_channels()
    except Exception as e:
        logger.warning(f"Could not load the clinics' WhatsApp numbers yet: {e}")

    if settings.REMINDERS_ENABLED:
        tasks.append(asyncio.create_task(reminder_loop()))
    else:
        logger.info("Reminder loop disabled; drive /api/v1/tasks/reminders instead")

    if settings.AUDIT_ENABLED and settings.AUDIT_WATCH_MINUTES > 0:
        tasks.append(asyncio.create_task(audit_loop()))

    yield

    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
    if tasks:
        logger.info("Background loops stopped")


app = FastAPI(title="Booking Bot", version="0.1.0", lifespan=lifespan)

app.include_router(webhook.router, prefix="/api/v1")
app.include_router(tasks.router, prefix="/api/v1")

# Management API for the dashboard. Every router except auth requires a
# token, declared on the router so a new endpoint cannot forget it.
app.include_router(auth.router, prefix="/api/v1")
app.include_router(appointments.router, prefix="/api/v1")
app.include_router(therapists.router, prefix="/api/v1")
app.include_router(treatments.router, prefix="/api/v1")
app.include_router(customers.router, prefix="/api/v1")
app.include_router(business.router, prefix="/api/v1")
# The owner's side: clinics, their numbers and logins.
app.include_router(platform.router, prefix="/api/v1")


# --- dashboard ---------------------------------------------------------------

DASHBOARD_DIR = Path(__file__).resolve().parent / "static" / "dashboard"

# Scripts and styles only from this server. Customer names and notes are
# shown on the dashboard and can originate outside the clinic, so even if
# one slipped past escaping, the browser would refuse to run it.
DASHBOARD_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    # Revalidate every load, so a dashboard updated by `git pull` is the one
    # the browser shows. Unchanged files still answer 304, so this is cheap.
    "Cache-Control": "no-cache",
}


@app.middleware("http")
async def dashboard_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/dashboard"):
        for name, value in DASHBOARD_HEADERS.items():
            response.headers[name] = value
    return response


app.mount("/dashboard", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard")


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/dashboard/")


@app.get("/health")
async def health():
    return {"status": "healthy"}
