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
import traceback
from pathlib import Path

# Progress bars render as thousands of lines in a CI log.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

MODEL_NAME = "distilgpt2"          # keep in step with prediction_service
# Every nltk.download() the backend makes, mapped to the resource path that
# proves it is usable: cmudict (syllables.py), wordnet and omw-1.4
# (routers/reading.py), words (nlp_service.py's spelling vocabulary).
NLTK_PACKAGES = {
    "cmudict": "corpora/cmudict",
    "wordnet": "corpora/wordnet",
    "omw-1.4": "corpora/omw-1.4",
    "words": "corpora/words",
}


def clear_stale_locks() -> int:
    """Delete huggingface_hub .lock files. Returns how many were removed.

    Always safe: a lock is a coordination file between concurrent downloads,
    and nothing else runs here.
    """
    removed = 0
    roots = [Path.home() / ".cache" / "huggingface", Path.home() / ".cache" / "hub"]
    # The container moves the cache with HF_HOME; sweeping only the default
    # location there would clear nothing and still hang on the real lock.
    if os.environ.get("HF_HOME"):
        roots.insert(0, Path(os.environ["HF_HOME"]))
    for root in roots:
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
                # Only on the last attempt, and only then: transformers wraps
                # a failed backend import as "Could not import module 'X'.
                # Are this object's requirements defined correctly?", which
                # names the model class and hides the actual ImportError.
                # The chain is where the real cause lives.
                traceback.print_exc()
                cause = exc.__cause__ or exc.__context__
                while cause is not None:
                    print(f"  caused by: {type(cause).__name__}: {cause}", flush=True)
                    cause = cause.__cause__ or cause.__context__
                return False
            time.sleep(backoff * i)
        else:
            print(f"  {label}: ok ({time.monotonic() - started:.1f}s)", flush=True)
            return True
    return False


def fetch_transformers() -> None:
    # torch first, and on its own line. transformers resolves model classes
    # lazily, so a broken torch surfaces as a ModuleNotFoundError naming
    # GPT2LMHeadModel — which reads like a missing model rather than a
    # missing backend and sends you looking in the wrong place entirely.
    import torch

    print(f"    torch {torch.__version__} (cuda={torch.cuda.is_available()})", flush=True)

    from transformers import AutoModelForCausalLM, AutoTokenizer

    AutoTokenizer.from_pretrained(MODEL_NAME)
    AutoModelForCausalLM.from_pretrained(MODEL_NAME)


def fetch_nltk() -> None:
    """Download each corpus AND prove it can be loaded afterwards.

    Two failures this guards against, both seen in the container build:

    1. NLTK_DATA pointing at a directory that does not exist yet.
       Downloader.default_download_dir() skips any path that is missing, so
       the download silently lands in ~/nltk_data instead — which works by
       luck until something changes the home directory.

    2. A corpus that downloads but does not resolve. wordnet ships as a zip,
       and when it is not unpacked nltk.data.find() raises LookupError at the
       first definition lookup — in production, not here. A successful
       download() return is not evidence the corpus is usable, so each one is
       loaded before this function claims success.
    """
    import nltk

    target = os.environ.get("NLTK_DATA")
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        if target not in nltk.data.path:
            nltk.data.path.insert(0, target)

    for package, resource in NLTK_PACKAGES.items():
        # raise_on_error so a silent False return can't look like success.
        nltk.download(package, quiet=True, raise_on_error=True, download_dir=target)

        try:
            nltk.data.find(resource)
        except LookupError:
            _unzip_corpus(package, target)
            # Let this one propagate: a corpus that still will not load after
            # being unpacked is a broken build, not something to paper over.
            nltk.data.find(resource)


def _unzip_corpus(package: str, target: str | None) -> None:
    """Unpack <package>.zip in place, for corpora NLTK left archived."""
    import zipfile

    import nltk

    for root in filter(None, [target, *nltk.data.path]):
        archive = Path(root) / "corpora" / f"{package}.zip"
        if archive.exists():
            print(f"    unpacking {archive}", flush=True)
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(archive.parent)
            return


def fetch_easyocr() -> None:
    import easyocr

    # Constructing the Reader is what pulls the detection and recognition
    # weights (~100MB). gpu=False matches the deployed CPU-only install.
    easyocr.Reader(["en"], gpu=False, verbose=False)


def fetch_languagetool() -> None:
    import language_tool_python

    # Constructing it downloads the LanguageTool server (~259MB) into
    # LTP_PATH or ~/.cache/language_tool_python, then starts a JVM on a
    # local port. Closing it matters in a build: a stray JVM would outlive
    # this step and hold the layer open.
    tool = language_tool_python.LanguageTool("en-US")
    try:
        assert tool.check("This are wrong."), "LanguageTool started but found nothing"
    finally:
        tool.close()


TARGETS = {
    "transformers": (fetch_transformers, f"{MODEL_NAME} (~350MB) for F29 prediction"),
    "nltk": (fetch_nltk, "cmudict syllables, WordNet definition fallback"),
    "easyocr": (fetch_easyocr, "detection + recognition weights (~100MB) for F05"),
    "languagetool": (fetch_languagetool, "LanguageTool server (~259MB) for F27 grammar; needs Java"),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        nargs="*",
        choices=sorted(TARGETS),
        help="Subset to fetch. Default: transformers and nltk. The container "
        "build fetches all four; CI leaves languagetool to its own cache.",
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
