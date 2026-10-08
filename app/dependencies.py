"""
Authentication for the management API.

Two kinds of people sign in. The owner of the service uses the password
in .env and may look at any clinic. Each clinic signs in with its own email
and password and sees only itself. Both get a signed token that the
dashboard sends with every later request, saying which kind they are and,
for a clinic, which clinic. Without a signing secret the API refuses
everything rather than defaulting to open.
"""
import hmac
import logging
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, Header, HTTPException
from jose import JWTError, jwt

from app.config import settings
from app.core import tenancy
from app.core.timeutils import now as clinic_now

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
OWNER = "owner"
CLINIC = "clinic"


@dataclass(frozen=True)
class Principal:
    """Who is signed in."""
    role: str
    user_id: str | None = None  # a clinic login's id
    business_id: str | None = None  # the clinic that login belongs to

    @property
    def is_owner(self) -> bool:
        return self.role == OWNER


def issue_token(subject: str = OWNER, role: str = OWNER, business_id=None) -> dict:
    """Sign a token for a successful login."""
    if not settings.JWT_SECRET:
        raise HTTPException(
            status_code=503,
            detail="JWT_SECRET is not configured; the API is closed",
        )

    expires_at = clinic_now() + timedelta(hours=settings.JWT_HOURS)
    claims = {"sub": subject, "role": role, "exp": expires_at}
    if business_id is not None:
        claims["bid"] = str(business_id)
    return {
        "access_token": jwt.encode(claims, settings.JWT_SECRET, algorithm=ALGORITHM),
        "token_type": "bearer",
        "expires_at": expires_at.isoformat(),
    }


def check_password(password: str) -> bool:
    """Compare against the owner's password without leaking its length."""
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


def current_user(token: str = Depends(bearer_token)) -> Principal:
    """
    Reject anything without a valid, unexpired token, and say who it is for.

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

    subject = claims.get("sub") or ""
    # Tokens from before clinic logins existed carry no role; they were the owner's.
    role = claims.get("role") or (OWNER if subject == OWNER else None)

    if role == OWNER and subject == OWNER:
        return Principal(OWNER)
    if role == CLINIC and subject.startswith("user:") and claims.get("bid"):
        return Principal(CLINIC, user_id=subject.removeprefix("user:"), business_id=claims["bid"])
    raise HTTPException(status_code=401, detail="Invalid token")


def require_owner(principal: Principal = Depends(current_user)) -> Principal:
    """Only the owner of the service: adding clinics, their logins and numbers."""
    if not principal.is_owner:
        raise HTTPException(status_code=403, detail="Only the owner of the service can do this")
    return principal


async def resolve_clinic(principal: Principal, requested: str | None) -> str:
    """
    Which clinic a request is about. A clinic login is always its own
    clinic, whatever it asks for. The owner chooses one with the
    X-Business-Id header, or gets the clinic named by BUSINESS_ID, which is
    what a single-clinic setup expects.
    """
    from app.services import accounts

    if principal.is_owner:
        business_id = requested or settings.BUSINESS_ID
        if not await accounts.business_exists(business_id):
            raise HTTPException(status_code=404, detail="No such clinic")
        return business_id

    if requested and requested != principal.business_id:
        raise HTTPException(status_code=403, detail="This login belongs to another clinic")
    # Checked on every request, so turning a login or a clinic off takes
    # effect at once rather than when the token runs out.
    problem = await accounts.login_problem(principal.user_id, principal.business_id)
    if problem:
        raise HTTPException(status_code=401, detail=problem)
    return principal.business_id


async def clinic_scope(
    principal: Principal = Depends(current_user),
    x_business_id: str | None = Header(default=None),
):
    """
    Serve the request's clinic for the length of the request, then let go.

    Every router holding clinic data depends on this, so no endpoint can
    forget to. Letting go matters where several requests share one task,
    as they do in tests, or the next would start out in this one's clinic.
    """
    business_id = await resolve_clinic(principal, x_business_id)
    with tenancy.acting_for(business_id):
        yield business_id
