"""Block until the backend's models are warm, then assert they are usable.

Run by .github/workflows/ci.yml between starting uvicorn and running pytest.
It lives here rather than inline in the workflow because the logic is three
nested conditions and a timeout, which YAML-inside-bash-inside-YAML renders
unreadable and unrunnable locally.

Why polling the port is not enough
----------------------------------
backend/main.py returns from startup immediately and warms spaCy +
LanguageTool, DistilGPT-2 and the classifier on three daemon threads behind
it. A "wait for :8000 to accept a connection" loop therefore succeeds within
a second, and the first /nlp/check in the suite then blocks for ~20s on a
warm-up lock — or worse, the suite runs against a backend reporting
grammar: unavailable and quietly stops covering F27.

/health is built for exactly this: it takes no warm-up lock, so it answers
during startup, and it reports each model as ready | loading | unavailable
plus a `warm` flag that flips once nothing is still loading.

Two separate checks, because they fail for different reasons
------------------------------------------------------------
`warm` going true means nothing is *still loading*. It does not mean
everything works — a model that failed outright reports `unavailable`, and
that also counts as "not loading any more". So warmth is the wait, and
readiness is the assertion:

    no Java              -> grammar: unavailable   -> F27 untested, silently
    missing joblib       -> classifier: unavailable -> /classify serves the
                            wordfreq fallback and a contract test would pass
                            against the wrong implementation

Usage:
    python scripts/ci_wait_for_health.py --url http://127.0.0.1:8000
    python scripts/ci_wait_for_health.py --allow-unavailable grammar
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def fetch_health(url: str, timeout: float = 10.0) -> dict | None:
    """One /health poll. None when the backend is not answering usefully yet.

    Every failure mode collapses to None on purpose: during startup a refused
    connection, a 502 from a proxy and a half-written response are all just
    "not yet", and the caller's only reasonable response to each is to wait.
    A malformed body is included — a Vite dev server answers every path with
    HTML and status 200.
    """
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=timeout) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, ConnectionError):
        return None

    return payload if isinstance(payload, dict) else None


def describe(models: dict) -> str:
    """"checks=ready grammar=loading ..." — one scannable line per poll."""
    return " ".join(f"{name}={state}" for name, state in sorted(models.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="Seconds to wait for warm. Generous by default: on a cold cache "
        "the runner is downloading LanguageTool (259MB) and DistilGPT-2 "
        "(~350MB) during this window.",
    )
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument(
        "--allow-unavailable",
        nargs="*",
        default=[],
        metavar="MODEL",
        help="Model names permitted to be 'unavailable' rather than 'ready'. "
        "Use sparingly — each one is a feature the suite stops covering.",
    )
    args = parser.parse_args()

    deadline = time.monotonic() + args.timeout
    last_seen = "nothing yet"

    while time.monotonic() < deadline:
        health = fetch_health(args.url)

        if health is None:
            print(f"  waiting for {args.url}/health to answer...", flush=True)
            time.sleep(args.interval)
            continue

        # The identity check backend/tests/conftest.py makes, for the same
        # reason: the four-person team repo also serves on :8000 with a
        # smaller /health, and running against it produces baffling failures.
        models = health.get("models")
        if not isinstance(models, dict) or "classifier" not in models:
            print(
                f"::error::{args.url} is serving something else - /health has no "
                f"models.classifier. Got: {json.dumps(health)[:400]}",
                flush=True,
            )
            return 1

        last_seen = describe(models)
        if health.get("warm"):
            print(f"  warm: {last_seen}", flush=True)
            break

        print(f"  loading: {last_seen}", flush=True)
        time.sleep(args.interval)
    else:
        print(
            f"::error::backend never warmed within {args.timeout:.0f}s. "
            f"Last state: {last_seen}",
            flush=True,
        )
        return 1

    # Warm, so nothing is still loading. Now: is any of it broken?
    allowed = set(args.allow_unavailable)
    broken = {
        name: state
        for name, state in models.items()
        if state != "ready" and name not in allowed
    }

    if broken:
        print(f"::error::models not ready: {describe(broken)}", flush=True)
        for name in broken:
            print(f"::error::  {name} -> {HINTS.get(name, 'see backend/main.py')}")
        return 1

    skipped = {n: s for n, s in models.items() if n in allowed and s != "ready"}
    if skipped:
        print(f"::warning::tolerated, so untested: {describe(skipped)}", flush=True)

    print(f"backend ready: {describe(models)}", flush=True)
    return 0


HINTS = {
    "grammar": "LanguageTool needs a JRE. apt-get install default-jre.",
    "checks": "spaCy's en_core_web_sm is a pinned wheel in backend/requirements.txt.",
    "prediction": "DistilGPT-2 downloads to the Hugging Face cache on first use.",
    "classifier": "backend/ml/classifier.joblib is missing; /classify would "
    "silently serve the wordfreq fallback.",
}


if __name__ == "__main__":
    sys.exit(main())
