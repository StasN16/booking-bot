"""Signing in to the management API."""
import logging
import time
from collections import deque

from fastapi import APIRouter, Depends, HTTPException, Request

from app.config import settings
from app.core.schemas.api import LoginRequest, MeOut, PasswordChange, TokenResponse
from app.dependencies import CLINIC, Principal, check_password, current_user, issue_token
from app.services import accounts

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


@router.get("/auth/status")
async def status():
    """
    Whether sign-in has been set up, for the login page to explain itself.

    Without it, a dashboard with no ADMIN_PASSWORD or JWT_SECRET could only
    answer "wrong password", forever. Says nothing beyond yes or no.
    """
    return {"configured": bool(settings.ADMIN_PASSWORD and settings.JWT_SECRET)}


@router.post("/auth/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request):
    """
    Exchange a password for a token: a clinic's email and password, or,
    with no email, the owner's password from .env.
    """
    key = client_key(request)

    wait = seconds_blocked(key)
    if wait:
        logger.warning(f"Login refused: too many failed attempts from {key}")
        raise HTTPException(
            status_code=429,
            detail="Too many wrong passwords. Try again later.",
            headers={"Retry-After": str(wait)},
        )

    if body.email and body.email.strip():
        user = await accounts.authenticate(body.email, body.password)
        if user is None:
            record_failure(key)
            # The same answer for an unknown email, a wrong password and a
            # login that is switched off: nothing to learn by guessing.
            logger.warning("Failed clinic login attempt")
            raise HTTPException(status_code=401, detail="Incorrect email or password")
        _failures.pop(key, None)
        logger.info(f"Clinic login succeeded for clinic {user.business_id}")
        return issue_token(subject=f"user:{user.id}", role=CLINIC, business_id=user.business_id)

    if not check_password(body.password):
        record_failure(key)
        # Deliberately vague: a wrong password and an unconfigured one look
        # the same from outside.
        logger.warning("Failed dashboard login attempt")
        raise HTTPException(status_code=401, detail="Incorrect password")

    _failures.pop(key, None)
    logger.info("Dashboard login succeeded")
    return issue_token()


@router.get("/auth/me", response_model=MeOut)
async def me(principal: Principal = Depends(current_user)):
    """Who is signed in, so the dashboard shows the owner's pages or a clinic's."""
    if principal.is_owner:
        return {"role": principal.role}
    problem = await accounts.login_problem(principal.user_id, principal.business_id)
    if problem:
        raise HTTPException(status_code=401, detail=problem)
    user = await accounts.get_login(principal.user_id)
    return {"role": principal.role, "email": user.email, "name": user.name,
            "business_id": str(user.business_id)}


@router.post("/auth/password")
async def change_password(body: PasswordChange, principal: Principal = Depends(current_user)):
    """A clinic login choosing its own password."""
    if principal.is_owner:
        raise HTTPException(status_code=400,
                            detail="The owner's password is set in the .env file, as ADMIN_PASSWORD")
    problem = await accounts.login_problem(principal.user_id, principal.business_id)
    if problem:
        raise HTTPException(status_code=401, detail=problem)
    try:
        await accounts.change_password(principal.user_id, body.current_password, body.new_password)
    except accounts.AccountError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "changed"}
