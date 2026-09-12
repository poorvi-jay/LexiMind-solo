"""
Shared fixtures for the M3 test suite.

Two kinds of test live here:

  * Unit tests (test_sm2.py, test_classifier.py, test_spacy_refusals.py) need
    nothing running and always execute.
  * Integration tests need the backend serving. They ask for the `api` fixture,
    which SKIPS when nothing is reachable rather than failing, so a plain
    `pytest` run on a fresh clone is still green.

The API base comes from LEXIMIND_API, defaulting to port 8000:

    set LEXIMIND_API=http://127.0.0.1:8001   # when 8000 is taken

Integration tests register throwaway accounts and write to the configured
database. Point them at a development database, never a real one.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

# backend.* imports must work no matter where pytest was invoked from. pytest
# only puts the test directory itself on sys.path, not the repository root.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

API_BASE = os.getenv("LEXIMIND_API", "http://127.0.0.1:8000")
TEST_PASSWORD = "TestPassw0rd!23"


@pytest.fixture(scope="session")
def api():
    """An HTTP client for a running backend, or a skip.

    The identity check is deliberate. Another LexiMind build — the four-person
    team repo — also serves on port 8000, with different routes and a smaller
    /health payload. Running these tests against it produces confusing
    failures, so anything that doesn't look like this project is skipped with
    an explanation instead.
    """
    client = httpx.Client(base_url=API_BASE, timeout=120)
    try:
        health = client.get("/health")
    except httpx.HTTPError as exc:
        client.close()
        pytest.skip(
            f"no backend at {API_BASE} ({exc.__class__.__name__}). Start it "
            f"(.claude/launch.json) and set LEXIMIND_API if it is not on port 8000."
        )

    # Anything that is not this project's /health is a skip, not a failure —
    # including a 200 that isn't JSON at all. A Vite dev server answers every
    # path with HTML and status 200, so parsing has to be allowed to fail.
    try:
        payload = health.json() if health.status_code == 200 else {}
    except ValueError:
        payload = {}

    if not isinstance(payload, dict) or "classifier" not in payload.get("models", {}):
        client.close()
        # Plain ASCII: this lands on a cp1252 console on Windows, where an em
        # dash comes out as a replacement character.
        pytest.skip(
            f"{API_BASE} is serving something else - /health has no models.classifier. "
            "This is probably another LexiMind build (the team repo also answers "
            "on :8000) or a frontend dev server; point LEXIMIND_API at this backend."
        )

    yield client
    client.close()


@pytest.fixture
def new_user(api):
    """Factory for throwaway accounts: returns (email, auth headers).

    A fresh account per test keeps counts starting from zero and means tests
    cannot see, or disturb, each other's rows.
    """

    def make(tag: str = "t"):
        email = f"pytest-{tag}-{uuid.uuid4().hex[:8]}@example.com"
        api.post(
            "/auth/register",
            json={"name": f"Pytest {tag}", "email": email, "password": TEST_PASSWORD},
        )
        response = api.post(
            "/auth/login", json={"email": email, "password": TEST_PASSWORD}
        )
        assert response.status_code == 200, f"could not log in as {email}"
        return email, {"Authorization": "Bearer " + response.json()["access_token"]}

    return make


@pytest.fixture
def db():
    """A database session, for the few assertions the API cannot make.

    Used to back-date rows and to recompute expected values straight from the
    tables — it is how a test can check a schedule a fortnight out, or a streak
    across days, without waiting.
    """
    from backend.database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def log_reading_session(api, headers, word_repeats: dict[str, int], **overrides):
    """Post one reading session. Helper, not a fixture, so tests can call it freely.

    Words are replayed `count` times each; three replays of a word promote it
    into the word bank (F49).
    """
    body = {
        "duration_seconds": 40,
        "words_read": 60,
        "total_words": 60,
        "hard_word_count": 3,
        "source_type": "paste",
        "simplified": False,
        "complexity_score": 8.0,
        "word_repeats": word_repeats,
    }
    body.update(overrides)
    return api.post("/sessions/reading", headers=headers, json=body)


def letters(count: int, prefix: str = "zz") -> list[str]:
    """`count` distinct all-letter words.

    All-letter on purpose: normalize_word strips non-letters from a token's
    edges, so "word01".."word25" would collapse to a single "word".
    """
    return [f"{prefix}{chr(97 + i)}{chr(97 + i)}" for i in range(count)]
