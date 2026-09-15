"""/health's readiness contract (M4 phase 4).

Two different consumers read this endpoint and want different fields, which
is why it has both `status` and `models`:

  * A platform health check keys on `status`. It stays "ok" whenever the API
    is serving, so a slow model or a missing Java never takes the instance out
    of rotation — every endpoint still answers.
  * The writing page keys on `models`. 'loading' is a "Loading..." state worth
    waiting on; 'unavailable' is a permanent note to show instead. Collapsing
    the two would make the page either spin forever or give up too early.

scripts/ci_wait_for_health.py depends on all of it, so a change here breaks
CI's gate rather than any test — worth pinning directly.
"""

from __future__ import annotations

VALID_STATES = {"ready", "loading", "unavailable"}

# The four warm-ups started in backend/main.py's lifespan.
EXPECTED_MODELS = {"checks", "grammar", "prediction", "classifier"}


def test_health_needs_no_token(api):
    """The only unauthenticated route that isn't part of signing in.

    A deploy health check cannot carry a bearer token, so this must never
    start requiring one.
    """
    response = api.get("/health")
    assert response.status_code == 200


def test_status_is_ok_while_serving(api):
    body = api.get("/health").json()
    assert body["status"] == "ok"
    assert body["version"]


def test_every_model_reports_a_known_state(api):
    models = api.get("/health").json()["models"]

    assert set(models) == EXPECTED_MODELS, (
        f"the model set changed: {set(models)}. "
        "scripts/ci_wait_for_health.py asserts on these names."
    )
    for name, state in models.items():
        assert state in VALID_STATES, f"{name} reported {state!r}"


def test_warm_means_nothing_is_still_loading(api):
    """`warm` is derived, and the derivation is the subtle part.

    It is all(state != "loading") — NOT all(state == "ready"). A model that
    failed outright is 'unavailable' and is never going to arrive, so holding
    `warm` back for it would make a client wait forever. CI relies on this:
    it waits for warm, then separately asserts readiness.
    """
    body = api.get("/health").json()
    expected = all(state != "loading" for state in body["models"].values())

    assert body["warm"] == expected, (
        "warm disagrees with the model states it is derived from: "
        f"{body['models']} gave warm={body['warm']}"
    )


def test_health_answers_without_taking_a_warm_up_lock(api):
    """It has to answer *during* startup — that is when it is asked.

    Not a timing assertion, which would be flaky. The models are warm by the
    time the suite runs, so this just confirms the endpoint is cheap and
    repeatable rather than doing work per call.
    """
    first = api.get("/health").json()
    second = api.get("/health").json()
    assert first["models"] == second["models"]


def test_health_identifies_this_build(api):
    """conftest.py and the CI gate both use models.classifier as an identity check.

    The four-person team repo also serves on port 8000 with a smaller /health,
    and running this suite against it produces baffling failures rather than a
    clear message. That check only works while this key exists.
    """
    assert "classifier" in api.get("/health").json()["models"]
