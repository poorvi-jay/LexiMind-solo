# LexiMind API image (M4 phase 7).
#
# Backend only. The frontend is a static Vite build and deploys as a static
# site; it has no reason to share an image with torch.
#
#   docker build -t leximind-api .
#   docker compose up            # see docker-compose.yml for the volume
#
# Layer order is the main design decision. From least to most frequently
# changed: system packages, Python packages, model weights, application code.
# Editing a router therefore rebuilds only the last few MB, never re-downloads
# ~700MB of weights, and never reinstalls torch.

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Every model cache pinned under one directory, so the weights are baked
    # into a known layer and none of them lands in a home directory or, worse,
    # on the /data volume, where the mount would hide them.
    HF_HOME=/opt/models/huggingface \
    NLTK_DATA=/opt/models/nltk_data \
    EASYOCR_MODULE_PATH=/opt/models/easyocr \
    LTP_PATH=/opt/models/language_tool_python \
    HF_HUB_DISABLE_TELEMETRY=1 \
    # The database lives on the mounted disk. Four slashes: an absolute path.
    # Three would resolve relative to /app and put accounts back inside the
    # container, where a redeploy deletes them.
    DATABASE_URL=sqlite:////data/leximind.db

# ── system packages ────────────────────────────────────────────────────
# poppler-utils        pdf2image shells out to it for /ocr/pdf
# default-jre-headless LanguageTool is a Java program (F27). Without it
#                      /nlp/check silently drops grammar and still returns 200.
# libglib2.0-0         OpenCV's one runtime dependency the slim image lacks
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        poppler-utils \
        default-jre-headless \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python packages ────────────────────────────────────────────────────
COPY backend/requirements.txt backend/requirements.txt

# CPU torch, pinned exactly, BEFORE the requirements resolve.
#
# easyocr pulls the default torch wheel, which on Linux bundles CUDA
# libraries nothing here can use: ~330MB of dead weight. The CPU wheels avoid
# that. An earlier CI attempt at this broke transformers ("Could not import
# module 'GPT2LMHeadModel'") because it installed an unpinned torch from
# --index-url and let the resolver choose. Two things differ here:
#
#   * versions are the exact pair the development machine runs and the suite
#     passes against (torch 2.12.1, torchvision 0.27.1);
#   * the +cpu local version label only exists on the PyTorch index, so pip
#     cannot quietly substitute the CUDA build of the same number from PyPI.
#
# If this pair ever stops working, the transformers prefetch below imports
# torch and loads a model, so the BUILD fails - it cannot ship broken.
RUN pip install \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        torch==2.12.1+cpu \
        torchvision==0.27.1+cpu \
    && pip install -r backend/requirements.txt

# ── runtime user and writable paths ────────────────────────────────────
# Created before the models are fetched so the prefetch can run AS this user
# and the weights are owned by it from the start. Downloading as root and then
# `chown -R`-ing would copy every file into a new layer and roughly double the
# image.
# Each cache directory is created explicitly, not left to the library. NLTK in
# particular skips any entry of NLTK_DATA that does not already exist and
# silently downloads to ~/nltk_data instead, which then works by accident
# until something changes HOME.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /opt/models/huggingface \
                /opt/models/nltk_data \
                /opt/models/easyocr \
                /opt/models/language_tool_python \
                /data \
    && chown -R app:app /opt/models /data

USER app

# ── model weights ──────────────────────────────────────────────────────
# Baked in at build time so the first request after every deploy does not pay
# for ~700MB of downloads, and so a failed download fails the build with a
# traceback instead of surfacing as "prediction: unavailable" in production.
COPY --chown=app:app scripts/prefetch_models.py scripts/prefetch_models.py
RUN python scripts/prefetch_models.py --only transformers nltk easyocr languagetool

# ── application ────────────────────────────────────────────────────────
COPY --chown=app:app backend/ backend/
COPY --chown=app:app scripts/ scripts/

# The classifier is committed, but it is also the one artifact whose absence
# fails silently: /classify falls back to wordfreq and still answers 200.
# Refuse to build an image that would do that.
RUN test -f backend/ml/classifier.joblib \
    || (echo "backend/ml/classifier.joblib is missing; /classify would serve the wordfreq fallback" && exit 1)

EXPOSE 8000

# status stays "ok" while models warm, by design, so this checks liveness —
# the same field a platform health check should key on. Readiness is
# scripts/ci_wait_for_health.py's job, not the container's.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import json,sys,urllib.request; \
sys.exit(0 if json.load(urllib.request.urlopen('http://127.0.0.1:' + __import__('os').environ.get('PORT','8000') + '/health', timeout=4))['status']=='ok' else 1)"

# `exec` so uvicorn is PID 1's direct replacement and receives SIGTERM on a
# redeploy, instead of a shell that swallows it until the kill timeout.
# PORT is set by hosts such as Render; 8000 otherwise.
CMD ["sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
