from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
from pathlib import Path
from dotenv import load_dotenv

# Explicit path so .env loads regardless of the process's working directory
load_dotenv(Path(__file__).resolve().parent / ".env")

from backend.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # create any missing tables (no Alembic — SQLite + create_all)
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

from backend.routers.ocr import router as ocr_router
from backend.routers.tts import router as tts_router
from backend.routers.reading import router as reading_router
from backend.routers.classify import router as classify_router  # import the classify router
app.include_router(ocr_router, tags=["OCR"])
app.include_router(tts_router, tags=["TTS"])
app.include_router(reading_router, tags=["Reading"])
app.include_router(classify_router, tags=["Classify"])  # include the classify router with a tag
@app.get("/health")
async def health():
    return {"status": "ok", "version": "5.0"}
