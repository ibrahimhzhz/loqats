"""
services/auth.py — The Security Layer
======================================
Responsibilities:
  1. Password hashing / verification  (passlib + bcrypt)
  2. JWT creation / decoding          (python-jose)
  3. FastAPI dependency get_current_user — validates the Bearer token on every
     protected request and returns the fully-hydrated User ORM object.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt as _bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from database import get_db
import models
import schemas

# ==============================================================================
# CONFIGURATION — load from environment variables in production.
# ==============================================================================

# openssl rand -hex 32
SECRET_KEY: str = os.getenv("SECRET_KEY", "")
ALGORITHM: str = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 60 * 8))  # 8 hours
JWT_ISSUER: str = os.getenv("JWT_ISSUER", "ats-backend")
JWT_AUDIENCE: str = os.getenv("JWT_AUDIENCE", "ats-client")
APP_ENV: str = os.getenv("APP_ENV", os.getenv("ENV", "development")).lower()
ALLOW_INSECURE_DEV_SECRET: bool = os.getenv("ALLOW_INSECURE_DEV_SECRET", "false").lower() in {
    "1",
    "true",
    "yes",
}
INSECURE_DEV_SECRET = "INSECURE_LOCAL_DEV_ONLY_CHANGE_ME"


def _is_weak_secret(secret: str) -> bool:
    return (
        not secret
        or secret.startswith("CHANGE_ME")
        or len(secret) < 32
    )


if _is_weak_secret(SECRET_KEY):
    if APP_ENV in {"local", "development", "dev", "test"} and ALLOW_INSECURE_DEV_SECRET:
        SECRET_KEY = INSECURE_DEV_SECRET
        print(
            "⚠️ Using explicit insecure development SECRET_KEY because "
            "ALLOW_INSECURE_DEV_SECRET=true. Never use this in shared/staging/production environments."
        )
    else:
        raise RuntimeError(
            "SECRET_KEY is missing or weak. Set a strong SECRET_KEY (>=32 chars). "
            "For local-only development, set ALLOW_INSECURE_DEV_SECRET=true explicitly."
        )

# ==============================================================================
# PASSWORD HASHING  (direct bcrypt — no passlib wrapper)
# ==============================================================================

def hash_password(plain_password: str) -> str:
    """Return a bcrypt hash of *plain_password*."""
    return _bcrypt.hashpw(
        plain_password.encode("utf-8"), _bcrypt.gensalt()
    ).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Return True if *plain_password* matches the stored *hashed_password*."""
    return _bcrypt.checkpw(
        plain_password.encode("utf-8"), hashed_password.encode("utf-8")
    )


# ==============================================================================
# JWT HELPERS
# ==============================================================================

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Encode *data* into a signed JWT.

    Args:
        data:          Arbitrary claims to embed (must include ``sub`` or similar).
        expires_delta: How long until the token expires. Defaults to
                       ACCESS_TOKEN_EXPIRE_MINUTES if not provided.

    Returns:
        A compact JWT string.
    """
    payload = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta if expires_delta else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload.update(
        {
            "exp": expire,
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
        }
    )
    if "sub" not in payload and payload.get("user_id") is not None:
        payload["sub"] = str(payload["user_id"])
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> schemas.TokenData:
    """
    Decode and validate a JWT.

    Returns:
        TokenData with user_id and company_id extracted from the claims.

    Raises:
        HTTPException 401 if the token is invalid or expired.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
        )
        user_id: Optional[int] = payload.get("user_id")
        company_id: Optional[int] = payload.get("company_id")
        if user_id is None:
            raise credentials_exception
        return schemas.TokenData(user_id=user_id, company_id=company_id)
    except JWTError:
        raise credentials_exception


# ==============================================================================
# FASTAPI DEPENDENCY — get_current_user
# ==============================================================================

# FastAPI will automatically extract the value after "Bearer " from the
# Authorization header and pass it to any function that declares this dependency.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> models.User:
    """
    FastAPI dependency that authenticates every incoming request.

    Usage in a route::

        @router.get("/jobs/")
        def list_jobs(current_user: models.User = Depends(get_current_user)):
            ...

    Flow:
      1. FastAPI extracts the ``Authorization: Bearer <token>`` header.
      2. The token is decoded and validated.
      3. The User is fetched from the DB to make sure the account still exists.
      4. The hydrated User object is injected into the route handler.

    Raises:
        HTTPException 401 — expired / tampered token, or user no longer exists.
    """
    token_data = decode_access_token(token)

    user = db.query(models.User).filter(models.User.id == token_data.user_id).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account not found.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


# ==============================================================================
# DATABASE HELPERS (used by the auth router)
# ==============================================================================

def authenticate_user(db: Session, email: str, password: str) -> Optional[models.User]:
    """
    Look up a user by email and verify the supplied password.

    Returns:
        The User if credentials are correct, otherwise None.
    """
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user
