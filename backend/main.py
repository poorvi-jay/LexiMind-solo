from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging
import os
import threading
from pathlib import Path
from dotenv import load_dotenv

# Nothing configured logging before, so the root logger sat at its WARNING
# default with no handler: every logger.info() in backend/services was dropped,
# including the lines that confirm LanguageTool and DistilGPT-2 finished loading.
# Install a handler at WARNING for the world, and lift just our own tree to INFO
# so transformers and friends don't fill the console. The level is checked at the
# originating logger, so backend.* records reach the root handler regardless of
# root's own level.
logging.basicConfig(level=logging.WARNING, format="%(levelname)s [%(name)s] %(message)s")
logging.getLogger("backend").setLevel(logging.INFO)

# Explicit path so .env loads regardless of the process's working directory
load_dotenv(Path(__file__).resolve().parent / ".env")

from backend.database import init_db
from backend.services import classifier_service, nlp_service, prediction_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # create any missing tables (no Alembic — SQLite + create_all)

    # spaCy's model, the phonetic index and LanguageTool's JVM together take
    # ~10-20s to build. Warming them on a daemon thread keeps startup instant;
    # the first /nlp/check just blocks on the same lock until it's ready.
    threading.Thread(target=nlp_service.warm_up, name="nlp-warmup", daemon=True).start()

    # DistilGPT-2 plus the prefix vocabulary, on its own thread so a slow model
    # load doesn't hold up the checks warming beside it.
    threading.Thread(
        target=prediction_service.warm_up, name="predict-warmup", daemon=True
    ).start()

    # The word difficulty classifier (F33). A cold joblib.load() measures ~2.9s,
    # which is small next to the other two but still far too slow to pay on the
    # first /classify — the Reading page calls it before playback starts.
    threading.Thread(
        target=classifier_service.warm_up, name="classify-warmup", daemon=True
    ).start()

    yield


app = FastAPI(title="LexiMind AI API", version="5.0", lifespan=lifespan)

configured_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "").split(",")
    if origin.strip()
]
allowed_origins = list(dict.fromkeys([
    *configured_origins,
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]))

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Word-Timings", "X-Duration-Ms"],
)

from backend.routers.auth import router as auth_router
from backend.routers.ocr import router as ocr_router
from backend.routers.tts import router as tts_router
from backend.routers.reading import router as reading_router
from backend.routers.classify import router as classify_router  # import the classify router
from backend.routers.writing import router as writing_router
from backend.routers.nlp import router as nlp_router
from backend.routers.sessions import router as sessions_router
from backend.routers.analytics import router as analytics_router
app.include_router(auth_router, tags=["Auth"])
app.include_router(ocr_router, tags=["OCR"])
app.include_router(tts_router, tags=["TTS"])
app.include_router(reading_router, tags=["Reading"])
app.include_router(classify_router, tags=["Classify"])  # include the classify router with a tag
app.include_router(writing_router, tags=["Writing"])
app.include_router(nlp_router, tags=["NLP"])
app.include_router(sessions_router, tags=["Sessions"])
app.include_router(analytics_router, tags=["Analytics"])
@app.get("/health")
async def health():
    """
    Liveness plus per-model readiness (M2 build guide §3.2 and §4.1).

    `status` stays "ok" whenever the API is serving, so a deploy health check
    keyed on it doesn't start failing the moment a model is slow or Java is
    missing — the app is up and every endpoint still answers. Readiness is a
    separate question and lives under `models`, each entry being:

        ready       usable now
        loading     still warming; ask again shortly
        unavailable will not work in this process (no Java, download failed…)

    The split matters to the writing page: 'loading' is a "Loading…" state worth
    waiting on, 'unavailable' is a permanent note to show instead. Nothing here
    takes a warm-up lock, so this answers immediately during startup — which is
    exactly when it gets asked.
    """
    models = {
        # F26-F28. spaCy gates all three checks, so this is the one signal.
        "checks": nlp_service.checks_readiness(),
        # F27. Needs a local Java install; see requirements.txt.
        "grammar": nlp_service.grammar_readiness(),
        # F29. DistilGPT-2, ~350MB on first run.
        "prediction": prediction_service.readiness(),
        # F33. 'unavailable' means /classify is serving the wordfreq fallback
        # because backend/ml/classifier.joblib is missing — run
        # backend/ml/train_classifier.py to build it.
        "classifier": classifier_service.readiness(),
    }
    return {
        "status": "ok",
        "version": "5.0",
        "models": models,
        # True once nothing is still warming. Something 'unavailable' does not
        # hold this back — it is never going to arrive, and a client waiting on
        # it would wait forever.
        "warm": all(state != "loading" for state in models.values()),
    }
