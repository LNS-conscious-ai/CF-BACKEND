"""
main.py — CF FastAPI Server · Day 3
Serves lns.life frontend · RUNTIME.md v1.1
Endpoints:
  POST /chat/stream  — SSE streaming CF responses
  POST /transcribe   — Deepgram voice input
  POST /speak        — ElevenLabs voice output
  GET  /health       — Railway health check
"""

import os, json, asyncio
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional
from cf_respond import cf_respond, CRISIS_PATTERNS, CRISIS_RESPONSE

app = FastAPI(title="CF Backend · LNS", version="1.0.0")

# ── CORS — lns.life frontend ──────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://www.lns.life",
        "https://lns.life",
        "https://lns-life-frontend.vercel.app",
        "http://localhost:3000",
        "http://localhost:8080",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEEPINFRA_API_KEY  = os.environ.get("DEEPINFRA_API_KEY", "")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID= os.environ.get("ELEVENLABS_VOICE_ID", "")
DEEPGRAM_API_KEY   = os.environ.get("DEEPGRAM_API_KEY", "")

# ── REQUEST MODELS ────────────────────────────────────────
class ChatRequest(BaseModel):
    message:    str
    history:    Optional[list] = []
    user_id:    Optional[str]  = "anonymous"
    session_id: Optional[str]  = "default"

class SpeakRequest(BaseModel):
    text: str

# ── HEALTH CHECK ──────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "CF Backend", "version": "1.0.0"}

@app.get("/")
async def root():
    return {"message": "CF Backend is live. lns.life is home."}

# ── CHAT STREAM — SSE ─────────────────────────────────────
@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """
    Main CF chat endpoint.
    Streams response as Server-Sent Events (SSE).
    lns.life frontend connects here.
    """
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    async def generate():
        try:
            # Run cf_respond (blocking) in thread pool
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: cf_respond(req.message, req.history or [])
            )

            # Stream metadata first
            meta = {
                "type":     "meta",
                "persona":  result["persona"],
                "intent":   result["intent_type"],
                "emotion":  result["emotion"],
                "crisis":   result["crisis"],
            }
            yield f"data: {json.dumps(meta)}\n\n"

            # Stream response text word by word
            words = result["response"].split(" ")
            for i, word in enumerate(words):
                chunk = word if i == 0 else " " + word
                payload = {"type": "text", "content": chunk}
                yield f"data: {json.dumps(payload)}\n\n"
                await asyncio.sleep(0.02)

            # Signal completion
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception as e:
            error = {"type": "error", "message": str(e)}
            yield f"data: {json.dumps(error)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",
            "Access-Control-Allow-Origin": "https://www.lns.life",
        }
    )

# ── CHAT NON-STREAM — simple JSON fallback ────────────────
@app.post("/chat")
async def chat(req: ChatRequest):
    """Simple JSON response — fallback if frontend needs it."""
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: cf_respond(req.message, req.history or [])
        )
        return JSONResponse(content=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── TRANSCRIBE — Deepgram voice input ────────────────────
@app.post("/transcribe")
async def transcribe(request: Request):
    """
    Accepts audio file, returns transcribed text.
    Uses Deepgram Nova-3.
    """
    if not DEEPGRAM_API_KEY:
        raise HTTPException(status_code=503, detail="Deepgram not configured")
    try:
        audio_data = await request.body()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.deepgram.com/v1/listen?model=nova-3&language=en",
                headers={
                    "Authorization": f"Token {DEEPGRAM_API_KEY}",
                    "Content-Type":  "audio/webm",
                },
                content=audio_data,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            transcript = (
                data.get("results", {})
                    .get("channels", [{}])[0]
                    .get("alternatives", [{}])[0]
                    .get("transcript", "")
            )
            return {"transcript": transcript}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── SPEAK — ElevenLabs voice output ──────────────────────
@app.post("/speak")
async def speak(req: SpeakRequest):
    """
    Converts CF text response to audio.
    Uses ElevenLabs Flash v2.5.
    """
    if not ELEVENLABS_API_KEY or not ELEVENLABS_VOICE_ID:
        raise HTTPException(status_code=503, detail="ElevenLabs not configured")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream",
                headers={
                    "xi-api-key":   ELEVENLABS_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "text":        req.text,
                    "model_id":    "eleven_flash_v2_5",
                    "voice_settings": {
                        "stability":        0.5,
                        "similarity_boost": 0.75,
                    },
                },
                timeout=30,
            )
            resp.raise_for_status()
            return StreamingResponse(
                resp.aiter_bytes(),
                media_type="audio/mpeg"
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ingest")
async def run_ingestion():
    """Trigger ChromaDB ingestion on Railway volume."""
    import subprocess
    result = subprocess.run(
        ["python3", "/app/ingest.py"],
        capture_output=True, text=True, timeout=600
    )
    return {
        "status": "done",
        "stdout": result.stdout[-3000:],
        "stderr": result.stderr[-1000:]
    }
