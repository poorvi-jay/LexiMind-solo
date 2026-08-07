import json

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from backend.services import tts_service

router = APIRouter()

def get_current_user():
    return {"id": "dev-user"}

class TTSRequest(BaseModel):
    text: str
    speed: float = 1.0
    voice: str = "en-GB-SoniaNeural"
    phrase_pauses: bool = True

class WordTTSRequest(BaseModel):
    word: str
    voice: str = "en-GB-SoniaNeural"

@router.post("/tts/generate")
async def tts_generate(
    req: TTSRequest,
    current_user: dict = Depends(get_current_user)
):
    return await tts_service.generate_tts(
        text=req.text,
        speed=req.speed,
        voice=req.voice,
        phrase_pauses=req.phrase_pauses
    )

@router.post("/tts/word")
async def tts_word(
    req: WordTTSRequest,
    current_user: dict = Depends(get_current_user)
):
    return await tts_service.generate_word_tts(req.word, req.voice)


@router.post("/tts/generate-fast")
async def tts_generate_fast(
    req: TTSRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Binary variant of /tts/generate for prefetching — returns raw audio bytes
    with word_timings/duration_ms in headers instead of base64 JSON, so the
    browser can skip the base64 decode step used by the JSON endpoint.
    """
    result = await tts_service.generate_tts_raw(
        text=req.text,
        speed=req.speed,
        voice=req.voice,
        phrase_pauses=req.phrase_pauses
    )
    return Response(
        content=result["audio_bytes"],
        media_type="audio/mpeg",
        headers={
            "X-Word-Timings": json.dumps(result["word_timings"]),
            "X-Duration-Ms": str(result["duration_ms"]),
        }
    )