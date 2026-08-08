"""
backend/routers/auth.py
F01 registration · F02 login + session persistence · F03 preferences storage
F04 preference updates

Endpoints: POST /auth/register, POST /auth/login, GET /auth/me,
           PATCH /auth/preferences
"""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.dependencies import get_current_user
from backend.models import User
from backend.services import auth_service

router = APIRouter()

# Superset of the PRD's list — M1's SettingsPage ships Lexend and Atkinson
# Hyperlegible, which the PRD (written earlier) doesn't mention.
ALLOWED_FONTS = ("Lexend", "Atkinson Hyperlegible", "Arial", "Verdana", "OpenDyslexic")

HEX_COLOR = r"^#(?:[0-9a-fA-F]{6})$"


# ── request / response models ──────────────────────────────────────────
class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class Preferences(BaseModel):
    """All fields optional — PATCH /auth/preferences is a partial update."""

    model_config = ConfigDict(extra="forbid")

    font: Literal[ALLOWED_FONTS] | None = None
    fontSize: int | None = Field(default=None, ge=14, le=28)
    lineSpacing: float | None = Field(default=None, ge=1.5, le=3.0)
    wordSpacing: int | None = Field(default=None, ge=0, le=10)
    overlay: str | None = Field(default=None, pattern=HEX_COLOR)
    highlightColor: str | None = Field(default=None, pattern=HEX_COLOR)
    focusRuler: bool | None = None
    darkMode: bool | None = None
    phrasePauses: bool | None = None
    ttsSpeed: float | None = Field(default=None, ge=0.5, le=2.0)
    distractionFree: bool | None = None


class UserOut(BaseModel):
    id: str
    name: str
    email: str
    created_at: datetime
    preferences: dict


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


def _serialize(user: User) -> UserOut:
    """password_hash is never included — PRD Section 4."""
    return UserOut(
        id=user.id,
        name=user.name,
        email=user.email,
        created_at=user.created_at,
        preferences=auth_service.preferences_dict(user),
    )


# ── endpoints ──────────────────────────────────────────────────────────
@router.post("/auth/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    """F01 — duplicate email returns 409, password hashed with bcrypt cost 12."""
    email = req.email.strip().lower()

    if db.query(User).filter(User.email == email).first() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists.")

    user = User(
        name=req.name.strip(),
        email=email,
        password_hash=auth_service.hash_password(req.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return AuthResponse(access_token=auth_service.create_access_token(user.id), user=_serialize(user))


@router.post("/auth/login", response_model=AuthResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    """F02 — 24h JWT on success, 401 on bad email or password."""
    email = req.email.strip().lower()
    user = db.query(User).filter(User.email == email).first()

    # Same message either way, so the response can't be used to enumerate accounts.
    if user is None or not auth_service.verify_password(req.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password.")

    user.last_active = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)

    return AuthResponse(access_token=auth_service.create_access_token(user.id), user=_serialize(user))


@router.get("/auth/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    """F03 — profile plus every preference, for hydrating the client on load."""
    return _serialize(current_user)


@router.patch("/auth/preferences", response_model=UserOut)
def update_preferences(
    req: Preferences,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """F04 — partial update of any preference field."""
    updates = req.model_dump(exclude_unset=True, exclude_none=True)
    if not updates:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No preference fields supplied.")

    auth_service.apply_preferences(current_user, updates)
    db.commit()
    db.refresh(current_user)

    return _serialize(current_user)
