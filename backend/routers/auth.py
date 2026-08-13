"""
backend/routers/auth.py
F01 registration · F02 login + session persistence · F03 preferences storage
F04 preference updates · password reset

Endpoints: POST /auth/register, POST /auth/login, GET /auth/me,
           PATCH /auth/preferences, POST /auth/forgot-password,
           GET /auth/reset-password/{token}, POST /auth/reset-password

The reset flow never reveals whether an email is registered. /auth/forgot-password
answers the same way for an unknown address as for a real one, because a
different answer would turn it into a way to test which emails have accounts.
"""

import os
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.dependencies import get_current_user
from backend.models import PasswordResetToken, User
from backend.services import auth_service, mailer

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


class ForgotPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=16, max_length=256)
    password: str = Field(min_length=8, max_length=128)


class StatusOut(BaseModel):
    status: str
    message: str


class ResetLinkCheck(BaseModel):
    valid: bool


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


# ── password reset ─────────────────────────────────────────────────────
def _reset_link(token: str) -> str:
    """The page the writer lands on. APP_BASE_URL lets a deployment override it."""
    base = os.getenv("APP_BASE_URL", "http://localhost:5173").rstrip("/")
    return f"{base}/auth/reset?token={quote(token)}"


def _live_token(db: Session, token: str) -> PasswordResetToken | None:
    """The row for this token if it is still redeemable, else None."""
    row = (
        db.query(PasswordResetToken)
        .filter(PasswordResetToken.token_hash == auth_service.hash_reset_token(token))
        .first()
    )
    if row is None or row.used_at is not None:
        return None

    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)  # SQLite returns naive UTC
    return row if expires > datetime.now(timezone.utc) else None


@router.post("/auth/forgot-password", response_model=StatusOut)
def forgot_password(req: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Send a reset link, if that address has an account.

    Always answers the same way. An unknown address, an account at its
    outstanding-token cap, and a mail server that is down all produce this exact
    response, so nothing here can be used to find out who has an account.
    """
    answer = StatusOut(
        status="ok",
        message="If that email has an account, a reset link is on its way.",
    )

    email = req.email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        return answer

    now = datetime.now(timezone.utc)
    outstanding = (
        db.query(PasswordResetToken)
        .filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > now,
        )
        .count()
    )
    if outstanding >= auth_service.MAX_OUTSTANDING_RESETS:
        return answer

    token, token_hash, expires_at = auth_service.create_reset_token()
    db.add(PasswordResetToken(user_id=user.id, token_hash=token_hash, expires_at=expires_at))
    db.commit()

    mailer.send(
        to=user.email,
        subject="Reset your LexiMind password",
        body=(
            f"Hi {user.name},\n\n"
            "You asked to reset your LexiMind password. Open this link to choose "
            f"a new one:\n\n{_reset_link(token)}\n\n"
            f"The link works once and expires in "
            f"{auth_service.RESET_TOKEN_TTL_MINUTES} minutes.\n\n"
            "If you didn't ask for this, you can ignore this email — your "
            "password stays as it is.\n"
        ),
    )
    return answer


@router.get("/auth/reset-password/{token}", response_model=ResetLinkCheck)
def check_reset_link(token: str, db: Session = Depends(get_db)):
    """Whether a link is still good, so the page can say so before anything is typed."""
    return ResetLinkCheck(valid=_live_token(db, token) is not None)


@router.post("/auth/reset-password", response_model=StatusOut)
def reset_password(req: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Set a new password and end every session that was open before now."""
    row = _live_token(db, req.token)
    if row is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "That reset link has expired or has already been used. Please request a new one.",
        )

    user = db.get(User, row.user_id)
    if user is None:  # account deleted between request and redemption
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That reset link is no longer valid.")

    now = datetime.now(timezone.utc)
    user.password_hash = auth_service.hash_password(req.password)
    user.password_changed_at = now
    row.used_at = now

    # Any other link that was still out there is now void: whoever prompted this
    # reset must not be able to redeem an earlier one they also requested.
    for other in user.reset_tokens:
        if other.id != row.id and other.used_at is None:
            other.used_at = now

    db.commit()
    return StatusOut(status="reset", message="Your password has been changed. You can log in now.")


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
