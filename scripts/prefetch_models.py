"""Download every model that would otherwise be fetched on first use.

Run by .github/workflows/ci.yml BEFORE uvicorn starts, and intended for the
M4 phase-7 container build too.

Why this exists
---------------
backend/main.py warms three models on daemon threads so startup returns
immediately. That is right for a server and wrong for CI, because a download
failure inside a daemon thread is flattened into one word on /health:

    prediction=unavailable   something raised; the reason is in the log
    prediction=loading       nothing raised and nothing returned — it hung

Neither is attributable without log-diving, and the hang is worse: there is no
timeout anywhere on the path through
prediction_service._get_model -> from_pretrained, so the warm-up thread can
block forever and /health will report "loading" until the job's own timeout
kills it. Downloading here instead means a failure is a failed step with a real
traceback, and the warm-up afterwards is a cache hit that returns in seconds.

Stale locks
-----------
huggingface_hub guards its cache with .lock files. A download killed partway
leaves them behind, and the next from_pretrained() blocks on one indefinitely.
A CI cache restored from a failed run therefore hangs rather than retries,
which is a very confusing failure. clear_stale_locks() removes them first;
they carry no state worth keeping.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

# Progress bars render as thousands of lines in a CI log.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

MODEL_NAME = "distilgpt2"          # keep in step with prediction_service
NLTK_PACKAGES = ["cmudict", "wordnet", "omw-1.4"]


def clear_stale_locks() -> int:
    """Delete huggingface_hub .lock files. Returns how many were removed.

    Always safe: a lock is a coordination file between concurrent downloads,
    and nothing else runs here.
    """
    removed = 0
    for root in (Path.home() / ".cache" / "huggingface", Path.home() / ".cache" / "hub"):
        if not root.exists():
            continue
        for lock in root.rglob("*.lock"):
            try:
                lock.unlink()
                removed += 1
            except OSError:
                pass
        # huggingface_hub keeps them under .locks/ directories too.
        for lockdir in root.rglob(".locks"):
            if lockdir.is_dir():
                shutil.rmtree(lockdir, ignore_errors=True)
                removed += 1
    return removed


def attempt(label: str, fn, retries: int = 3, backoff: float = 5.0) -> bool:
    """Run fn(), retrying transient failures. True on success.

    The Hub rate-limits unauthenticated clients, and a GitHub runner is a
    very unauthenticated client sharing an egress IP with a great many
    others, so a first-try 429 is ordinary rather than exceptional.
    """
    for i in range(1, retries + 1):
        started = time.monotonic()
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — report and retry, whatever it is
            print(f"  {label}: attempt {i}/{retries} failed: "
                  f"{type(exc).__name__}: {exc}", flush=True)
            if i == retries:
                return False
            time.sleep(backoff * i)
        else:
            print(f"  {label}: ok ({time.monotonic() - started:.1f}s)", flush=True)
            return True
    return False


def fetch_transformers() -> None:
    from transformers import AutoModelForCausalLM, AutoTokenizer

    AutoTokenizer.from_pretrained(MODEL_NAME)
    AutoModelForCausalLM.from_pretrained(MODEL_NAME)


def fetch_nltk() -> None:
    import nltk

    for package in NLTK_PACKAGES:
        # raise_on_error so a silent False return can't look like success.
        nltk.download(package, quiet=True, raise_on_error=True)


def fetch_easyocr() -> None:
    import easyocr

    # Constructing the Reader is what pulls the detection and recognition
    # weights (~100MB). gpu=False matches the deployed CPU-only install.
    easyocr.Reader(["en"], gpu=False, verbose=False)


TARGETS = {
    "transformers": (fetch_transformers, f"{MODEL_NAME} (~350MB) for F29 prediction"),
    "nltk": (fetch_nltk, "cmudict syllables, WordNet definition fallback"),
    "easyocr": (fetch_easyocr, "detection + recognition weights (~100MB) for F05"),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        nargs="*",
        choices=sorted(TARGETS),
        help="Subset to fetch. Default: transformers and nltk. EasyOCR is "
        "opt-in because nothing tests /ocr/image until M4 phase 4.",
    )
    parser.add_argument(
        "--keep-locks",
        action="store_true",
        help="Skip the stale-lock sweep (debugging only).",
    )
    args = parser.parse_args()

    if not args.keep_locks:
        removed = clear_stale_locks()
        print(f"stale huggingface locks cleared: {removed}", flush=True)

    wanted = args.only or ["transformers", "nltk"]
    failed = []

    for name in wanted:
        fn, description = TARGETS[name]
        print(f"{name}: {description}", flush=True)
        if not attempt(name, fn):
            failed.append(name)

    if failed:
        print(f"::error::prefetch failed: {', '.join(failed)}", flush=True)
        return 1

    print("all model weights present", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
