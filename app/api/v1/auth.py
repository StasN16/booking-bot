"""Signing in to the management API."""
import logging
import time
from collections import deque

from fastapi import APIRouter, HTTPException, Request

from app.core.schemas.api import LoginRequest, TokenResponse
from app.dependencies import check_password, issue_token

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])

# Wrong passwords allowed from one address within the window before that
# address has to wait. Generous for a person mistyping, useless for guessing.
MAX_FAILURES = 10
WINDOW_SECONDS = 15 * 60
# Addresses remembered at once; past this the oldest are forgotten, so a
# flood of addresses cannot grow memory without limit.
MAX_TRACKED = 5000

_failures: dict[str, deque] = {}


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def seconds_blocked(key: str) -> int:
    """How long `key` must still wait, or 0 if it may try now."""
    attempts = _failures.get(key)
    if not attempts:
        return 0
    now = time.monotonic()
    while attempts and now - attempts[0] > WINDOW_SECONDS:
        attempts.popleft()
    if len(attempts) < MAX_FAILURES:
        return 0
    return int(WINDOW_SECONDS - (now - attempts[0])) + 1


def record_failure(key: str):
    if key not in _failures and len(_failures) >= MAX_TRACKED:
        _failures.pop(next(iter(_failures)))
    _failures.setdefault(key, deque()).append(time.monotonic())


def reset():
    """Forget every recorded failure. For tests."""
    _failures.clear()


@router.post("/auth/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request):
    """Exchange the clinic password for a token."""
    key = client_key(request)

    wait = seconds_blocked(key)
    if wait:
        logger.warning(f"Login refused: too many failed attempts from {key}")
        raise HTTPException(
            status_code=429,
            detail="Too many wrong passwords. Try again later.",
            headers={"Retry-After": str(wait)},
        )

    if not check_password(body.password):
        record_failure(key)
        # Deliberately vague: a wrong password and an unconfigured one look
        # the same from outside.
        logger.warning("Failed dashboard login attempt")
        raise HTTPException(status_code=401, detail="Incorrect password")

    _failures.pop(key, None)
    logger.info("Dashboard login succeeded")
    return issue_token()
