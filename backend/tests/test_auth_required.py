"""Every protected route rejects an anonymous caller (M4 phase 5).

One table, one assertion per route. The value is less in any single case than
in the table being *complete* and staying that way: test_every_route_is_listed
compares it against the running app's own OpenAPI schema, so a route added
without a decision about its auth fails the build rather than shipping open.

Why the schema and not the source
---------------------------------
Counting `@router.get(...)` decorators undercounts this app. /writing/session
is registered imperatively:

    router.add_api_route("/writing/session", log_writing_session,
                         methods=["PATCH"], deprecated=True, ...)

which no decorator grep finds. The app knows its own routes; source patterns
only approximate them.

401, never 403
--------------
backend/dependencies.py builds HTTPBearer with auto_error=False specifically
so a missing Authorization header reaches our handler and returns 401 with a
WWW-Authenticate challenge, rather than HTTPBearer's default 403. The
difference is not cosmetic — 401 means "authenticate and retry", 403 means
"authenticated but forbidden", and a client that retries on 403 loops. It is
one keyword argument away from silently regressing, so it is asserted here.
"""

from __future__ import annotations

import pytest

# Placeholders for path parameters. They are never resolved: the 401 has to
# happen before the handler runs, so a well-formed but nonexistent id is
# exactly right — if one of these ever 404s instead, the route is looking the
# row up before checking who is asking.
PATH_PARAMS = {
    "{token}": "not-a-real-reset-token",
    "{document_id}": "00000000-0000-0000-0000-000000000000",
}

# Every route that requires a bearer token. Verified against a live backend:
# each returns 401 when called with no Authorization header.
PROTECTED: list[tuple[str, str]] = [
    ("GET", "/analytics/difficult-words"),
    ("GET", "/analytics/reading"),
    ("GET", "/analytics/summary"),
    ("GET", "/analytics/writing"),
    ("GET", "/auth/me"),
    ("PATCH", "/auth/preferences"),
    ("POST", "/classify"),
    ("POST", "/nlp/check"),
    ("POST", "/nlp/predict"),
    ("POST", "/nlp/predict/phrase"),
    ("POST", "/ocr/image"),
    ("POST", "/ocr/pdf"),
    ("POST", "/reading/complexity"),
    ("POST", "/reading/define"),
    ("POST", "/reading/simplify"),
    ("POST", "/reading/syllabify"),
    ("POST", "/sessions/reading"),
    ("POST", "/sessions/writing"),
    ("POST", "/tts/generate"),
    ("POST", "/tts/generate-fast"),
    ("POST", "/tts/word"),
    ("GET", "/wordbank"),
    ("GET", "/wordbank/drill"),
    ("POST", "/wordbank/drill/result"),
    ("GET", "/wordbank/stats"),
    ("GET", "/writing/autosave"),
    ("PATCH", "/writing/autosave"),
    ("GET", "/writing/documents"),
    ("POST", "/writing/documents"),
    ("DELETE", "/writing/documents/{document_id}"),
    ("GET", "/writing/documents/{document_id}"),
    ("PATCH", "/writing/session"),  # deprecated alias of POST /sessions/writing
]

# Routes that are open by design. Each needs a reason, because "it isn't in
# the protected list" is how something ships unauthenticated by accident.
PUBLIC: dict[tuple[str, str], str] = {
    ("GET", "/health"): "liveness and model readiness; a deploy health check "
    "cannot carry a token",
    ("POST", "/auth/register"): "creating the account that tokens come from",
    ("POST", "/auth/login"): "exchanging credentials for a token",
    ("POST", "/auth/forgot-password"): "the caller has lost their credentials",
    ("POST", "/auth/reset-password"): "authorised by the reset token in the body, "
    "not by a session",
    ("GET", "/auth/reset-password/{token}"): "the reset page asks whether a token "
    "is still valid before rendering the form",
}


def _url(path: str) -> str:
    for placeholder, value in PATH_PARAMS.items():
        path = path.replace(placeholder, value)
    return path


def _call(api, method: str, path: str, headers: dict | None = None):
    """Send `method` to `path` with a minimal body where one is expected.

    The body is deliberately invalid ({} satisfies no schema here). If auth is
    checked first the response is 401; if validation runs first it is 422.
    Asserting 401 therefore also pins the ordering — an anonymous caller
    cannot use validation errors to map the request schemas.
    """
    kwargs = {"headers": headers or {}}
    if method in ("POST", "PATCH", "PUT"):
        kwargs["json"] = {}
    return api.request(method, _url(path), **kwargs)


@pytest.mark.parametrize("method,path", PROTECTED, ids=lambda v: v)
def test_rejects_a_caller_with_no_token(api, method, path):
    response = _call(api, method, path)

    assert response.status_code == 401, (
        f"{method} {path} answered {response.status_code} to an anonymous "
        f"caller. 401 is required: 403 would be HTTPBearer's default, which "
        f"means auto_error=False was dropped in backend/dependencies.py; "
        f"422 would mean the body is validated before the token is checked; "
        f"2xx means the route is open."
    )

    # The challenge that tells a client this is retryable with credentials.
    assert response.headers.get("www-authenticate") == "Bearer", (
        f"{method} {path} returned 401 without a WWW-Authenticate: Bearer "
        f"header; got {response.headers.get('www-authenticate')!r}"
    )


@pytest.mark.parametrize("method,path", PROTECTED, ids=lambda v: v)
def test_rejects_a_malformed_token(api, method, path):
    """A present-but-junk token must fail the same way a missing one does.

    Separate from the case above because they take different branches in
    get_current_user: no header at all short-circuits on `credentials is
    None`, while a garbage token reaches decode_token_claims. A route could
    pass one and fail the other.
    """
    response = _call(
        api, method, path, headers={"Authorization": "Bearer not.a.real.jwt"}
    )

    assert response.status_code == 401, (
        f"{method} {path} answered {response.status_code} to a malformed "
        f"bearer token; expected 401."
    )


@pytest.mark.parametrize("method,path", sorted(PUBLIC), ids=lambda v: v)
def test_public_routes_stay_reachable(api, method, path):
    """The other half of the sweep.

    Without this, the protected table could be "completed" by making
    everything require a token — including login, which no one could then
    obtain. Any non-401 is a pass: these routes answer 200, or 422 to the
    deliberately empty body above.
    """
    response = _call(api, method, path)

    assert response.status_code != 401, (
        f"{method} {path} now requires a token, but is public by design: "
        f"{PUBLIC[(method, path)]}."
    )


def test_every_route_is_listed(api):
    """The table covers the app exactly — no gaps, no stale rows.

    This is what makes the sweep self-maintaining. Adding a route without
    adding it to PROTECTED or PUBLIC fails here, naming the route, so the
    decision about its auth is forced at the time it is written rather than
    discovered later.
    """
    spec = api.get("/openapi.json")
    assert spec.status_code == 200, "could not read the app's OpenAPI schema"

    live = {
        (method.upper(), path)
        for path, operations in spec.json()["paths"].items()
        for method in operations
        if method in ("get", "post", "patch", "delete", "put")
    }
    listed = set(PROTECTED) | set(PUBLIC)

    unlisted = live - listed
    assert not unlisted, (
        # Plain ASCII: assertion text lands on a cp1252 console on Windows,
        # where an em dash comes out as a replacement character.
        "routes exist that this sweep does not cover - add each to PROTECTED "
        "(the usual answer) or to PUBLIC with a reason: "
        + ", ".join(f"{m} {p}" for m, p in sorted(unlisted, key=lambda r: r[1]))
    )

    stale = listed - live
    assert not stale, (
        "the table lists routes the app no longer serves; remove them: "
        + ", ".join(f"{m} {p}" for m, p in sorted(stale, key=lambda r: r[1]))
    )
