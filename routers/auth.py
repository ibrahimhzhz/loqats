"""
routers/auth.py — Registration & Login Endpoints
=================================================
POST /auth/register  → Create a new Company + its first admin User
POST /auth/login     → Standard OAuth2 password flow → returns JWT
GET  /auth/me        → Returns the current authenticated user's profile
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from database import get_db
import models
import schemas
from services.auth import (
    hash_password,
    authenticate_user,
    create_access_token,
    get_current_user,
)

router = APIRouter(prefix="/auth", tags=["Auth"])


# ==============================================================================
# REGISTER
# ==============================================================================

@router.post("/register", response_model=schemas.UserResponse, status_code=status.HTTP_201_CREATED)
def register(payload: schemas.UserCreate, db: Session = Depends(get_db)):
    """
    Register a new company and its first admin user in a single request.

    - If a company with the given name already exists the endpoint returns 409.
    - If the email is already taken the endpoint returns 409.
    """
    # 1. Guard: unique email
    if db.query(models.User).filter(models.User.email == payload.email).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Email '{payload.email}' is already registered.",
        )

    # 2. Guard: unique company name
    if db.query(models.Company).filter(models.Company.name == payload.company_name).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Company '{payload.company_name}' is already registered.",
        )

    # 3. Create tenant (Company)
    company = models.Company(name=payload.company_name)
    db.add(company)
    db.flush()  # Populates company.id before creating the user

    # 4. Create first user as admin
    user = models.User(
        company_id=company.id,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        role="admin",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ==============================================================================
# LOGIN  (standard OAuth2 form: username + password)
# ==============================================================================

@router.post("/login", response_model=schemas.Token)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """
    Authenticate with email + password. Returns a Bearer JWT on success.

    The ``username`` field of the OAuth2 form is used to carry the **email**.
    This is standard OAuth2 behaviour — most HTTP clients and OpenAPI UIs
    label the field "username" by default.
    """
    user = authenticate_user(db, email=form_data.username, password=form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token(
        data={"user_id": user.id, "company_id": user.company_id}
    )
    return schemas.Token(access_token=token)


# ==============================================================================
# ME  (introspection endpoint)
# ==============================================================================

@router.get("/me", response_model=schemas.UserResponse)
def get_me(current_user: models.User = Depends(get_current_user)):
    """Return the profile of the currently authenticated user."""
    return current_user
