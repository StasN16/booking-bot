"""
Authentication for the management API.

The clinic owner signs in with one password and receives a token that the
dashboard sends on every later request. Both the password and the signing
secret come from configuration, and if either is missing the API refuses
everything rather than defaulting to open.
"""
import hmac
import logging
from datetime import timedelta

from fastapi import Depends, Header, HTTPException
from jose import JWTError, jwt

from app.config import settings
from app.core.timeutils import now as clinic_now

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"


def issue_token(subject: str = "owner") -> dict:
    """Sign a token for a successful login."""
    if not settings.JWT_SECRET:
        raise HTTPException(
            status_code=503,
            detail="JWT_SECRET is not configured; the API is closed",
        )

    expires_at = clinic_now() + timedelta(hours=settings.JWT_HOURS)
    token = jwt.encode(
        {"sub": subject, "exp": expires_at},
        settings.JWT_SECRET,
        algorithm=ALGORITHM,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires_at.isoformat(),
    }


def check_password(password: str) -> bool:
    """Compare against the configured password without leaking its length."""
    if not settings.ADMIN_PASSWORD or not password:
        return False
    return hmac.compare_digest(password, settings.ADMIN_PASSWORD)


def bearer_token(authorization: str = Header(default=None)) -> str:
    """Pull the token out of an Authorization header."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Not authenticated")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Expected a bearer token")
    return token


def current_user(token: str = Depends(bearer_token)) -> str:
    """
    Reject anything without a valid, unexpired token.

    Every management endpoint depends on this, so the failure mode when
    configuration is missing is a closed door rather than an open one.
    """
    if not settings.JWT_SECRET:
        raise HTTPException(
            status_code=503,
            detail="JWT_SECRET is not configured; the API is closed",
        )
    try:
        claims = jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
    except JWTError as e:
        # The reason is for the log, not for whoever is knocking.
        logger.info(f"Rejected a token: {e}")
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    subject = claims.get("sub")
    if not subject:
        raise HTTPException(status_code=401, detail="Invalid token")
    return subject
