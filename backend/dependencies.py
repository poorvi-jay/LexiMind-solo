"""
backend/dependencies.py
Shared FastAPI dependencies.

get_current_user lives here rather than in routers/auth.py so that the M1
routers (ocr, tts, reading) can depend on it without importing the auth router
— which would import them back through main.py.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import User
from backend.services import auth_service

# auto_error=False so a missing header reaches our handler and returns 401
# ("Not authenticated"), rather than HTTPBearer's default 403.
_bearer = HTTPBearer(auto_error=False)

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or expired token.",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the bearer token to a User row, or raise 401."""
    if credentials is None or not credentials.credentials:
        raise _UNAUTHENTICATED

    claims = auth_service.decode_token_claims(credentials.credentials)
    if claims is None:
        raise _UNAUTHENTICATED

    user = db.get(User, claims["sub"])
    if user is None:
        raise _UNAUTHENTICATED  # token signed for a since-deleted account

    # A password reset ends every session that was open before it.
    if auth_service.token_predates_password_change(claims, user.password_changed_at):
        raise _UNAUTHENTICATED

    return user
