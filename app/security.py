from datetime import datetime, timedelta, timezone
from hashlib import sha256
from hmac import compare_digest, new as hmac_new
import secrets

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import User

password_hash = PasswordHash.recommended()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")


def hash_password(value: str) -> str:
    return password_hash.hash(value)


def verify_password(value: str, hashed: str) -> bool:
    return password_hash.verify(value, hashed)


CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def make_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


def digest_code(code: str) -> str:
    return hmac_new(get_settings().code_secret.encode(), code.strip().upper().encode(), sha256).hexdigest()


def code_matches(code: str, expected: str) -> bool:
    return compare_digest(digest_code(code), expected)


def create_token(user: User) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=get_settings().access_token_minutes)
    return jwt.encode({"sub": user.id, "role": user.role, "exp": expires}, get_settings().jwt_secret, algorithm="HS256")


def current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_error = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication")
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
