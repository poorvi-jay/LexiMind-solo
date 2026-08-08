"""
backend/services/auth_service.py
Password hashing, JWT issue/decode, and the pref_* <-> frontend key mapping.

Deviation from the build guide: it specifies passlib[bcrypt], but passlib 1.7.4
(unmaintained since 2020) raises during backend detection against bcrypt 5.x.
We call bcrypt directly instead — same algorithm, same cost factor, one less
abandoned dependency.
"""

import os
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

ALGORITHM = "HS256"
TOKEN_TTL_HOURS = 24
BCRYPT_ROUNDS = 12  # PRD Section 4: "bcrypt cost 12"

# bcrypt silently truncates at 72 bytes; 5.x raises instead. Truncate explicitly
# so the behaviour is ours and not the library's.
MAX_PASSWORD_BYTES = 72


def _secret_key() -> str:
    """Read at call time, not import time, so .env load order doesn't matter."""
    key = os.getenv("SECRET_KEY")
    if not key:
        raise RuntimeError("SECRET_KEY is not set — JWTs cannot be signed.")
    return key


# ── passwords ──────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    pw = password.encode("utf-8")[:MAX_PASSWORD_BYTES]
    return bcrypt.hashpw(pw, bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    pw = password.encode("utf-8")[:MAX_PASSWORD_BYTES]
    try:
        return bcrypt.checkpw(pw, password_hash.encode("utf-8"))
    except ValueError:
        return False  # malformed hash in the row — treat as a failed login


# ── tokens ─────────────────────────────────────────────────────────────
def create_access_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(hours=TOKEN_TTL_HOURS),
    }
    return jwt.encode(payload, _secret_key(), algorithm=ALGORITHM)


def decode_access_token(token: str) -> str | None:
    """Return the user id, or None if the token is invalid/expired/malformed."""
    try:
        payload = jwt.decode(token, _secret_key(), algorithms=[ALGORITHM])
    except JWTError:
        return None
    user_id = payload.get("sub")
    return user_id if isinstance(user_id, str) else None


# ── preferences ────────────────────────────────────────────────────────
# The DB uses the PRD's snake_case pref_* columns; the frontend has used these
# camelCase keys since M1 (frontend/src/hooks/usePreferences.js). Mapping here,
# in one place, keeps the Phase 2 localStorage -> backend migration a rename-free
# drop-in on the React side.
PREF_FIELDS = {
    "font": "pref_font",
    "fontSize": "pref_font_size",
    "lineSpacing": "pref_line_spacing",
    "wordSpacing": "pref_word_spacing",
    "overlay": "pref_overlay",
    "highlightColor": "pref_highlight_color",
    "focusRuler": "pref_focus_ruler",
    "darkMode": "pref_dark_mode",
    "phrasePauses": "pref_phrase_pauses",
    "ttsSpeed": "pref_tts_speed",
    "distractionFree": "pref_distraction_free",
}


def preferences_dict(user) -> dict:
    """Serialise a User's pref_* columns using the frontend's key names."""
    return {key: getattr(user, column) for key, column in PREF_FIELDS.items()}


def apply_preferences(user, updates: dict) -> None:
    """Partial update — only keys actually supplied are written."""
    for key, value in updates.items():
        column = PREF_FIELDS.get(key)
        if column is not None:
            setattr(user, column, value)
