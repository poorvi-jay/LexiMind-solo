"""The seven /auth routes (M4 phase 4).

Covers registration, login, the profile, preferences and the whole password
reset flow — including the properties that exist for security rather than for
features, which are the ones most likely to be "simplified" away by someone
who does not know why they are there:

  * /auth/forgot-password answers identically for a known and an unknown
    address, so it cannot be used to discover who has an account;
  * login says "Incorrect email or password" for both a wrong password and a
    nonexistent user, for the same reason;
  * a reset token is single-use and expiring;
  * completing a reset invalidates tokens issued before it, which is the only
    way a stateless JWT session can be ended by a password change.

Reset tokens are stored as SHA-256, so a test cannot read a real one back out
of the table. It mints its own instead — auth_service.create_reset_token()
hands back the plaintext and the hash together, and the row is inserted
through the `db` fixture. That exercises the real redemption path with a token
the test knows.
"""

from __future__ import annotations

import time
import uuid

import pytest

from backend.tests.conftest import TEST_PASSWORD

NEW_PASSWORD = "Rewritten!Passw0rd"


@pytest.fixture
def fresh_email() -> str:
    return f"pytest-auth-{uuid.uuid4().hex[:10]}@example.com"


def _register(api, email: str, password: str = TEST_PASSWORD, name: str = "Test Writer"):
    return api.post(
        "/auth/register", json={"name": name, "email": email, "password": password}
    )


def _mint_reset_token(db, user_id: str, *, expired: bool = False, used: bool = False):
    """Insert a reset row and return the plaintext token that opens it."""
    from datetime import datetime, timedelta, timezone

    from backend.models import PasswordResetToken
    from backend.services import auth_service

    token, token_hash, expires = auth_service.create_reset_token()
    if expired:
        expires = datetime.now(timezone.utc) - timedelta(minutes=1)

    row = PasswordResetToken(
        user_id=user_id,
        token_hash=token_hash,
        # Naive UTC: the column has no timezone and the route re-attaches UTC
        # when SQLite hands the value back.
        expires_at=expires.replace(tzinfo=None),
        used_at=datetime.now(timezone.utc).replace(tzinfo=None) if used else None,
    )
    db.add(row)
    db.commit()
    return token


# ── register ───────────────────────────────────────────────────────────

def test_register_returns_a_usable_token_and_the_new_user(api, fresh_email):
    response = _register(api, fresh_email)
    assert response.status_code == 201, response.text[:200]

    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == fresh_email
    assert body["user"]["id"]
    assert isinstance(body["user"]["preferences"], dict)
    assert "password" not in response.text.lower(), "the response mentions a password"

    # The token has to work immediately — the client goes straight to the app.
    me = api.get(
        "/auth/me", headers={"Authorization": "Bearer " + body["access_token"]}
    )
    assert me.status_code == 200
    assert me.json()["id"] == body["user"]["id"]


def test_the_same_email_cannot_register_twice(api, fresh_email):
    assert _register(api, fresh_email).status_code == 201
    assert _register(api, fresh_email).status_code == 409


def test_a_short_password_is_rejected(api, fresh_email):
    """Eight characters is the documented floor."""
    assert _register(api, fresh_email, password="short12").status_code == 422


def test_a_malformed_email_is_rejected(api):
    assert _register(api, "not-an-email").status_code == 422


# ── login ──────────────────────────────────────────────────────────────

def test_login_exchanges_credentials_for_a_token(api, fresh_email):
    _register(api, fresh_email)

    response = api.post(
        "/auth/login", json={"email": fresh_email, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200
    assert response.json()["access_token"]


def test_a_wrong_password_and_an_unknown_account_are_indistinguishable(
    api, fresh_email
):
    """Same status and same message, so login cannot enumerate accounts."""
    _register(api, fresh_email)

    wrong_password = api.post(
        "/auth/login", json={"email": fresh_email, "password": "WrongPassw0rd!"}
    )
    no_such_user = api.post(
        "/auth/login",
        json={"email": f"nobody-{uuid.uuid4().hex[:8]}@example.com",
              "password": TEST_PASSWORD},
    )

    assert wrong_password.status_code == no_such_user.status_code == 401
    assert wrong_password.json() == no_such_user.json(), (
        "the two failures differ, which reveals whether an account exists: "
        f"{wrong_password.json()} vs {no_such_user.json()}"
    )


# ── preferences ────────────────────────────────────────────────────────

def test_preferences_are_saved_and_returned_by_me(api, new_user):
    """PATCH answers with the user, and /auth/me must agree afterwards."""
    _, headers = new_user("prefs")

    patched = api.patch(
        "/auth/preferences", headers=headers, json={"font": "OpenDyslexic"}
    )
    assert patched.status_code == 200
    assert patched.json()["preferences"]["font"] == "OpenDyslexic"

    assert api.get("/auth/me", headers=headers).json()["preferences"]["font"] == (
        "OpenDyslexic"
    )


def test_an_empty_preferences_patch_is_rejected(api, new_user):
    """400 rather than a silent no-op, so a broken client is visible."""
    _, headers = new_user("prefs-empty")
    response = api.patch("/auth/preferences", headers=headers, json={})
    assert response.status_code == 400


def test_preferences_are_validated(api, new_user):
    """font is a Literal and overlay is pattern-matched - both must hold."""
    _, headers = new_user("prefs-bad")

    assert api.patch(
        "/auth/preferences", headers=headers, json={"font": "Comic Sans"}
    ).status_code == 422
    assert api.patch(
        "/auth/preferences", headers=headers, json={"overlay": "not-a-colour"}
    ).status_code == 422


# ── forgot password ────────────────────────────────────────────────────

def test_forgot_password_never_reveals_whether_an_account_exists(api, fresh_email):
    """The headline property of this route.

    An unknown address, an account at its outstanding-token cap and a dead
    mail server all produce this identical answer.
    """
    _register(api, fresh_email)

    known = api.post("/auth/forgot-password", json={"email": fresh_email})
    unknown = api.post(
        "/auth/forgot-password",
        json={"email": f"nobody-{uuid.uuid4().hex[:8]}@example.com"},
    )

    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json(), (
        "a known and an unknown address answer differently, which turns this "
        "route into an account-enumeration oracle"
    )


# ── reset ──────────────────────────────────────────────────────────────

def test_a_garbage_reset_link_reports_itself_invalid(api):
    """The page asks before rendering the form, and gets a flag, not an error."""
    response = api.get("/auth/reset-password/" + "x" * 40)
    assert response.status_code == 200
    assert response.json()["valid"] is False


def test_a_live_reset_link_reports_itself_valid(api, db, new_user):
    _, headers = new_user("reset-check")
    user_id = api.get("/auth/me", headers=headers).json()["id"]

    token = _mint_reset_token(db, user_id)

    assert api.get(f"/auth/reset-password/{token}").json()["valid"] is True


def test_a_reset_changes_the_password(api, db, new_user):
    email, headers = new_user("reset-do")
    user_id = api.get("/auth/me", headers=headers).json()["id"]
    token = _mint_reset_token(db, user_id)

    response = api.post(
        "/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}
    )
    assert response.status_code == 200

    assert api.post(
        "/auth/login", json={"email": email, "password": NEW_PASSWORD}
    ).status_code == 200
    assert api.post(
        "/auth/login", json={"email": email, "password": TEST_PASSWORD}
    ).status_code == 401, "the old password still works"


def test_a_reset_token_is_single_use(api, db, new_user):
    _, headers = new_user("reset-reuse")
    user_id = api.get("/auth/me", headers=headers).json()["id"]
    token = _mint_reset_token(db, user_id)

    assert api.post(
        "/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}
    ).status_code == 200

    again = api.post(
        "/auth/reset-password", json={"token": token, "password": "Another!Passw0rd"}
    )
    assert again.status_code == 400, "a used reset link worked a second time"


def test_an_expired_reset_token_is_refused(api, db, new_user):
    _, headers = new_user("reset-expired")
    user_id = api.get("/auth/me", headers=headers).json()["id"]
    token = _mint_reset_token(db, user_id, expired=True)

    assert api.get(f"/auth/reset-password/{token}").json()["valid"] is False
    assert api.post(
        "/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}
    ).status_code == 400


def test_a_reset_ends_sessions_opened_before_it(api, db, new_user):
    """The point of users.password_changed_at.

    A JWT is stateless, so a reset cannot delete one. Refusing every token
    issued before the change is how someone who has lost control of their
    account actually gets it back — otherwise whoever holds the old token
    keeps it until expiry.

    The one-second pause is not flakiness padding: `iat` is a whole-second
    Unix timestamp, so auth_service compares at second resolution and
    deliberately keeps a token issued *within* the same second as the reset.
    Without the pause this test would be asserting against that allowance.
    """
    _, headers = new_user("reset-sessions")
    user_id = api.get("/auth/me", headers=headers).json()["id"]
    assert api.get("/auth/me", headers=headers).status_code == 200

    time.sleep(1.1)
    token = _mint_reset_token(db, user_id)
    assert api.post(
        "/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}
    ).status_code == 200

    assert api.get("/auth/me", headers=headers).status_code == 401, (
        "a token issued before the password reset still works"
    )
