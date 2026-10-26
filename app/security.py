from datetime import datetime, timedelta, timezone
from hashlib import sha256
from hmac import compare_digest, new as hmac_new
import secrets

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import User

password_hash = PasswordHash.recommended()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)
# The browser UI keeps its token in this HTTP-only cookie instead of script-readable storage.
SESSION_COOKIE = "attendance_session"


def hash_password(value: str) -> str:
    return password_hash.hash(value)


def verify_password(value: str, hashed: str) -> bool:
    return password_hash.verify(value, hashed)


# No 0/O, 1/I/L, or 5/S, so a code read off a projector can't be mistyped.
CODE_ALPHABET = "ABCDEFGHJKMNPQRTUVWXYZ2346789"
CODE_LENGTH = 10


def make_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def normalize_code(code: str) -> str:
    """Students may type lowercase, spaces, or dashes (the board shows the code in two groups)."""
    return "".join(character for character in code.upper() if character.isalnum())


def digest_code(code: str) -> str:
    return hmac_new(get_settings().code_secret.encode(), normalize_code(code).encode(), sha256).hexdigest()


def code_matches(code: str, expected: str) -> bool:
    return compare_digest(digest_code(code), expected)


def make_qr_token(session_id: str, code_version: int, session_expires_at: datetime) -> tuple[str, datetime]:
    """Signed, short-lived, single-use QR token. Returns the token and its expiry."""
    issued_at = datetime.now(timezone.utc)
    expires = min(issued_at + timedelta(seconds=get_settings().qr_ttl_seconds), session_expires_at)
    claims = {
        "session_id": session_id,
        "code_version": code_version,
        "nonce": secrets.token_urlsafe(16),
        "iat": issued_at,
        "exp": expires,
    }
    return jwt.encode(claims, get_settings().code_secret, algorithm="HS256"), expires


def create_token(user: User) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=get_settings().access_token_minutes)
    return jwt.encode({"sub": user.id, "role": user.role, "exp": expires}, get_settings().jwt_secret, algorithm="HS256")


def current_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Authenticate from the Authorization header, or from the session cookie set by the browser login."""
    credentials_error = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication")
    token = token or request.cookies.get(SESSION_COOKIE)
    if not token:
        raise credentials_error
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
        user_id = payload.get("sub")
    except jwt.InvalidTokenError as exc:
        raise credentials_error from exc
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise credentials_error
    return user


def require_role(role: str):
    def dependency(user: User = Depends(current_user)) -> User:
        if user.role != role:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return dependency
