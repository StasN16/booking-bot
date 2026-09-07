"""Signing in to the management API."""
import logging

from fastapi import APIRouter, HTTPException

from app.core.schemas.api import LoginRequest, TokenResponse
from app.dependencies import check_password, issue_token

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])


@router.post("/auth/login", response_model=TokenResponse)
async def login(body: LoginRequest):
    """Exchange the clinic password for a token."""
    if not check_password(body.password):
        # Deliberately vague: a wrong password and an unconfigured one look
        # the same from outside.
        logger.warning("Failed dashboard login attempt")
        raise HTTPException(status_code=401, detail="Incorrect password")

    logger.info("Dashboard login succeeded")
    return issue_token()
