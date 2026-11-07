"""
Storing and checking clinic passwords.

Only a salted scrypt hash is kept, so a copy of the database does not
give away anyone's password. scrypt is in Python's standard library and
deliberately slow and memory-hungry, which is what makes guessing costly.
"""
import base64
import hashlib
import hmac
import secrets

# About 16MB and a few tens of milliseconds per check: unnoticeable when
# signing in, expensive for anyone trying millions of guesses.
N, R, P = 2 ** 14, 8, 1
KEY_BYTES = 32
MIN_LENGTH = 8


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=N, r=R, p=P, dklen=KEY_BYTES)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """Whether `password` is the one `stored` was made from. Never raises."""
    try:
        scheme, n, r, p, salt, digest = (stored or "").split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        candidate = hashlib.scrypt(
            (password or "").encode("utf-8"),
            salt=base64.b64decode(salt),
            n=int(n), r=int(r), p=int(p), dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, expected)


# Checked when the email matches no login, so that a wrong email takes as
# long as a wrong password and does not reveal which emails exist.
DECOY_HASH = hash_password(secrets.token_urlsafe(16))


def generate_password() -> str:
    """Twelve characters, easy to copy and impractical to guess."""
    return secrets.token_urlsafe(9)
